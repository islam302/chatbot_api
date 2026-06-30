"""Knowledge-gap capture: store the meaningful questions the bot couldn't answer.

When a chat answer is not confident (the tenant's data had no good match), we
optionally save the question as an ``UnansweredQuestion`` — but only after an AI
filter keeps the ones that are (a) within the tenant's domain and (b) genuinely
worth answering. Trivia, greetings and off-topic questions are dropped.

Capture runs OFF the request path (Celery in production, a daemon thread
otherwise) so chat latency is unaffected.
"""

from __future__ import annotations

import json
import logging
import re
import threading

from django.conf import settings
from django.utils import timezone

from ..models import UnansweredQuestion

logger = logging.getLogger(__name__)

_GREETINGS = {
    "hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "test",
    "مرحبا", "مرحبًا", "اهلا", "أهلا", "السلام عليكم", "شكرا", "شكرًا", "تمام", "تست",
}


def normalize_question(q: str) -> str:
    return " ".join((q or "").lower().split())[:500]


def _is_trivial(q: str) -> bool:
    s = (q or "").strip().lower()
    if len(s) < 6:
        return True
    if s.startswith("/"):  # slash command
        return True
    if s in _GREETINGS:
        return True
    return False


def _extract_json(text: str) -> str:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    return match.group(0) if match else "{}"


def classify_question(question: str, user) -> tuple[bool, str]:
    """Ask the LLM whether this gap is in-domain and worth answering.

    Returns ``(relevant, reason)``. On any failure defaults to keeping it
    (better to surface a gap than silently lose it).
    """
    from .chatbot_config import resolve_config
    from .llm import get_backend
    from .retrieval import search_chunks

    cfg = resolve_config(user)
    org = (cfg.company_name or "this business").strip() or "this business"

    try:
        hits = search_chunks(question, user=user, threshold=0.0, top_k=3)
        domain = "\n---\n".join(h.content[:300] for h in hits)
    except Exception:
        domain = ""

    system = (
        "You decide whether a customer's unanswered question is worth saving as a "
        "knowledge gap for a business to answer later. Reply ONLY with compact JSON: "
        '{"relevant": true|false, "reason": "short reason"}.'
    )
    user_prompt = (
        f"Business: {org}\n"
        f"Samples of what this business knows about:\n{domain or '(little or none yet)'}\n\n"
        f"Customer's question: {question}\n\n"
        f"Set relevant=true ONLY if the question is (a) within this business's "
        f"domain/specialization, AND (b) a genuine, meaningful question worth "
        f"answering (NOT a greeting, thanks, test, spam, or nonsense). "
        f"Otherwise relevant=false."
    )
    try:
        text = get_backend().complete(system, user_prompt)
        data = json.loads(_extract_json(text))
        return bool(data.get("relevant")), str(data.get("reason", ""))[:300]
    except Exception:
        logger.warning("Unanswered-question classifier failed; keeping by default")
        return True, "kept automatically (classifier unavailable)"


def record_unanswered(user, question: str, language: str = "") -> UnansweredQuestion | None:
    """Filter + persist one unanswered question. Returns the row or None if dropped."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if _is_trivial(question):
        return None

    key = normalize_question(question)
    existing = UnansweredQuestion.objects.filter(user=user, question_key=key).first()
    if existing is not None:
        # Seen before — bump the counter, skip a second classification.
        existing.occurrences += 1
        existing.last_asked_at = timezone.now()
        existing.save(update_fields=["occurrences", "last_asked_at", "updated_at"])
        return existing

    relevant, reason = classify_question(question, user)
    if not relevant:
        return None

    return UnansweredQuestion.objects.create(
        user=user,
        question=question[:2000],
        question_key=key,
        language=language or "",
        reason=reason,
        last_asked_at=timezone.now(),
    )


def _safe_record(user_id, question, language):
    from django.contrib.auth import get_user_model
    from django.db import connection

    try:
        user = get_user_model().objects.filter(pk=user_id).first()
        if user is not None:
            record_unanswered(user, question, language)
    except Exception:
        logger.exception("Failed to record unanswered question")
    finally:
        connection.close()


def capture_unanswered(user, question: str, language: str = "") -> None:
    """Entry point from the chat view — never blocks the response, never raises."""
    if not getattr(settings, "UNANSWERED_CAPTURE", True):
        return
    if user is None or not getattr(user, "is_authenticated", False):
        return
    if _is_trivial(question):
        return

    if getattr(settings, "INGESTION_MODE", "sync") == "celery":
        try:
            from ..tasks import record_unanswered_question_task

            record_unanswered_question_task.delay(str(user.pk), question, language)
            return
        except Exception:
            logger.exception("Could not enqueue unanswered-question task; using thread")

    threading.Thread(
        target=_safe_record, args=(user.pk, question, language), daemon=True
    ).start()
