"""Capture questions the bot could not confidently answer (knowledge gaps).

Pipeline (all off the chat request path — see ``dispatch_capture``):

1. Normalise the question into a stable ``question_key`` for de-duplication.
2. Ask a cheap LLM whether it is a *genuine* question the business should be
   able to answer (drops greetings, chit-chat, abuse, off-topic noise). The
   kept-reason is stored for the reviewer.
3. ``get_or_create`` per tenant on ``question_key``; a repeat bumps
   ``occurrences`` and ``last_asked_at`` instead of inserting a duplicate.

Dispatch mode mirrors document ingestion (``UNANSWERED_CAPTURE_MODE``):
``sync`` (default, in-request), ``thread`` (daemon thread), or ``celery``.
Capture must never break a chat reply, so callers wrap it defensively and the
AI filter fails-open only for transient errors (see ``classify_gap``).
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass

from django.conf import settings
from django.db.models import F, Max
from django.utils import timezone

from ..models import (
    DocumentChunk,
    DocumentStatus,
    SourceType,
    UnansweredQuestion,
    UnansweredStatus,
    UploadedDocument,
)
from .embeddings import EmbeddingError, embed_one
from .llm import LLMError, get_backend
from .vector_store import backfill_vectors_for_document

logger = logging.getLogger(__name__)

# One shared per-tenant document holds every answer a reviewer resolves, so the
# RAG pipeline retrieves them like any other knowledge (see resolve_to_knowledge).
QA_DOC_FILENAME = "Resolved Questions (Q&A)"

# Keep the normalised key within the model's column width.
_KEY_MAX = 500
_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS = re.compile(r"\s+")


def normalize_question(text: str) -> str:
    """A stable de-dup key: lowercased, punctuation stripped, whitespace collapsed.

    Deliberately simple (no LLM/embeddings) so it is deterministic and cheap.
    "What are your hours??" and "what are your hours" collapse to one key.
    """
    text = (text or "").strip().lower()
    text = _PUNCT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return text[:_KEY_MAX]


@dataclass
class GapVerdict:
    keep: bool
    reason: str = ""


_FILTER_SYSTEM = (
    "You triage messages a support chatbot could not answer, to build a list of "
    "knowledge gaps for the business to fill. KEEP only SELF-CONTAINED questions "
    "that clearly name what they are about and that a business should be able to "
    "answer from its own knowledge (products, services, policies, refunds, "
    "returns, warranties, pricing, discounts, hours, locations, shipping, "
    "booking, account, technical how-to, support). "
    "Answer NO for: greetings, thanks, small talk, one-word/test messages, "
    "gibberish, or abuse. ALSO answer NO for messages that only make sense as a "
    "follow-up to the conversation: clarifications and meta questions (e.g. 'what "
    "did I ask?', 'what do you mean?', 'which one?', 'انا سألت عن ايه؟'), and "
    "fragments that refer to something earlier by a pronoun ('it', 'this', "
    "'that', 'دي', 'ده', 'هو', 'هي') without naming a concrete subject (e.g. "
    "'when is it available?', 'امتا هيتوفر', 'how much is it?'). "
    "The recent conversation is given ONLY to judge whether the latest message is "
    "self-contained — do NOT keep a fragment just because the context reveals its "
    "subject. When in doubt about a clearly self-contained business question, "
    "answer YES. Reply with ONLY 'YES: <short reason>' or 'NO: <short reason>'."
)


def _render_history_for_filter(history, max_turns: int = 4) -> str:
    if not history:
        return ""
    lines: list[str] = []
    for msg in list(history)[-max_turns * 2:]:
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        who = "User" if msg.get("role", "user") == "user" else "Assistant"
        lines.append(f"{who}: {content}")
    return "\n".join(lines)


def classify_gap(question: str, language: str = "", history=None) -> GapVerdict:
    """AI filter: is this a self-contained knowledge gap worth capturing?

    Fails OPEN (keep=True) on LLM errors so a provider hiccup never silently
    drops real gaps; the reviewer can still dismiss noise. Returns keep=False
    only when the model explicitly rejects the message.
    """
    convo = _render_history_for_filter(history)
    prompt = (
        f"Recent conversation (context only):\n{convo or '(none)'}\n\n"
        f"Latest message to judge (language={language or 'unknown'}):\n{question}"
    )
    try:
        llm = get_backend()
        text = llm.complete(_FILTER_SYSTEM, prompt).strip()
    except LLMError as exc:
        logger.warning("Unanswered AI filter unavailable, keeping by default: %s", exc)
        return GapVerdict(keep=True, reason="kept (AI filter unavailable)")
    except Exception:
        logger.exception("Unanswered AI filter crashed, keeping by default")
        return GapVerdict(keep=True, reason="kept (AI filter error)")

    verdict, _, reason = text.partition(":")
    keep = verdict.strip().upper().startswith("Y")
    return GapVerdict(keep=keep, reason=reason.strip()[:500])


def capture_unanswered(
    *, user, question: str, language: str = "", history=None
) -> UnansweredQuestion | None:
    """Filter, then record/increment a knowledge gap for ``user``.

    Returns the row (created or bumped), or ``None`` when the AI filter rejected
    the message or the input was empty. ``history`` (recent turns) lets the filter
    drop context-dependent follow-ups. Safe to call synchronously in tests.
    """
    key = normalize_question(question)
    if not key:
        return None

    verdict = classify_gap(question, language, history=history)
    if not verdict.keep:
        logger.info(
            "Dropped non-gap message for user %s: %r (reason: %s)",
            user.pk,
            question[:120],
            verdict.reason,
        )
        return None

    obj, created = UnansweredQuestion.objects.get_or_create(
        user=user,
        question_key=key,
        defaults={
            "question": question.strip(),
            "language": language or "",
            "reason": verdict.reason,
            "last_asked_at": timezone.now(),
        },
    )
    if not created:
        # Bump atomically; don't overwrite the reviewer's status or edits.
        UnansweredQuestion.objects.filter(pk=obj.pk).update(
            occurrences=F("occurrences") + 1,
            last_asked_at=timezone.now(),
        )
        obj.refresh_from_db(fields=["occurrences", "last_asked_at"])
    return obj


def _run_in_thread(user_id, question, language, history) -> None:
    from django.contrib.auth import get_user_model
    from django.db import connection

    try:
        user = get_user_model().objects.get(pk=user_id)
        capture_unanswered(user=user, question=question, language=language, history=history)
    except Exception:
        logger.exception("Background unanswered capture failed for user %s", user_id)
    finally:
        connection.close()


def dispatch_capture(*, user, question: str, language: str = "", history=None) -> None:
    """Record a knowledge gap per ``UNANSWERED_CAPTURE_MODE``; never raises.

    The AI filter adds an LLM round-trip, so production should run this off the
    request path (``thread`` or ``celery``). ``sync`` keeps tests deterministic.
    ``history`` is passed to the filter so context-dependent follow-ups are dropped.
    """
    mode = getattr(settings, "UNANSWERED_CAPTURE_MODE", "sync").lower()
    try:
        if mode == "celery":
            from ..tasks import capture_unanswered_task

            capture_unanswered_task.delay(str(user.pk), question, language, history)
            return
        if mode == "thread":
            threading.Thread(
                target=_run_in_thread,
                args=(user.pk, question, language, history),
                daemon=True,
            ).start()
            return
        capture_unanswered(user=user, question=question, language=language, history=history)
    except Exception:
        # Capturing a gap must never break the chat reply.
        logger.exception("dispatch_capture failed for user %s", getattr(user, "pk", None))


def _qa_document(user) -> UploadedDocument:
    """The tenant's single 'Resolved Q&A' knowledge document (created on demand)."""
    doc, _ = UploadedDocument.objects.get_or_create(
        uploaded_by=user,
        filename=QA_DOC_FILENAME,
        defaults={
            "source_type": SourceType.FILE,
            "processing_status": DocumentStatus.COMPLETED,
            "is_active": True,
        },
    )
    # Keep it active/completed so retrieval always includes it.
    if not doc.is_active or doc.processing_status != DocumentStatus.COMPLETED:
        doc.is_active = True
        doc.processing_status = DocumentStatus.COMPLETED
        doc.save(update_fields=["is_active", "processing_status", "updated_at"])
    return doc


def resolve_to_knowledge(*, unanswered: UnansweredQuestion, answer: str, user) -> DocumentChunk:
    """Turn a reviewer's answer into retrievable knowledge and mark the gap answered.

    Writes a ``Q: ... A: ...`` chunk into the tenant's Resolved-Q&A document and
    embeds it with the same pipeline as uploads, so the next time the question is
    asked the RAG search finds it. Re-resolving the same gap updates its chunk in
    place (keyed by the gap id) instead of duplicating.

    Raises ``EmbeddingError`` if the answer can't be embedded (caller decides how
    to surface it); in that case nothing is marked answered.
    """
    answer = (answer or "").strip()
    if not answer:
        raise ValueError("An answer is required to resolve into knowledge.")

    content = f"Q: {unanswered.question}\nA: {answer}"
    vector, model = embed_one(content)  # may raise EmbeddingError

    doc = _qa_document(user)
    key = f"unanswered:{unanswered.pk}"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    existing = doc.chunks.filter(source_id=key).first()
    if existing is not None:
        existing.content = content
        existing.content_hash = content_hash
        existing.embedding = vector
        existing.embedding_model = model
        existing.save(
            update_fields=["content", "content_hash", "embedding", "embedding_model", "updated_at"]
        )
        chunk = existing
    else:
        next_pos = (doc.chunks.aggregate(m=Max("position"))["m"] or -1) + 1
        chunk = DocumentChunk.objects.create(
            document=doc,
            position=next_pos,
            content=content,
            content_hash=content_hash,
            source_id=key,
            embedding=vector,
            embedding_model=model,
            metadata={"origin": "resolved_unanswered", "unanswered_id": str(unanswered.pk)},
        )

    # Make it immediately searchable under the pgvector backend (no-op on numpy).
    backfill_vectors_for_document(doc.pk)

    unanswered.status = UnansweredStatus.ANSWERED
    unanswered.save(update_fields=["status", "updated_at"])
    return chunk
