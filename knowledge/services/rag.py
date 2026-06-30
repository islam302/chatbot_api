"""RAG-only answer pipeline with multilingual and multi-dialect support.

Retrieves relevant document chunks and passes them to an LLM
for question answering in the user's language and dialect.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Iterable

from .chatbot_config import (
    build_no_data_prompt,
    build_system_prompt,
    resolve_config,
    resolve_threshold,
    resolve_top_k,
)
from .llm import LLMError, get_backend
from .retrieval import ChunkHit, search_chunks

logger = logging.getLogger(__name__)


# The system prompt is assembled per-user from ChatbotConfig — see
# services/chatbot_config.py::build_system_prompt.


class RagUnavailable(RuntimeError):
    """Raised when RAG pipeline cannot run."""


# Hidden marker the LLM appends to its reply when it cannot answer from the
# provided knowledge. Stripped before the answer is returned; its presence is
# the authoritative "the bot declined" signal used to capture knowledge gaps.
_NO_DATA_TAG = "[[NO_DATA]]"


def _strip_no_data(text: str) -> tuple[bool, str]:
    """Split the decline sentinel from the reply → ``(answered, clean_text)``."""
    if text and _NO_DATA_TAG in text:
        return False, text.replace(_NO_DATA_TAG, "").strip()
    return True, text


@dataclass
class AnswerResult:
    answer: str
    source: str  # "rag"
    source_id: str = ""
    sources: list[dict] = field(default_factory=list)
    chunk_hits: list[ChunkHit] = field(default_factory=list)
    confident: bool = True
    # Did the bot actually answer from the tenant's data (vs. decline / no data)?
    # This — NOT `confident` — decides whether the question is a knowledge gap.
    answered: bool = True
    # Token usage of the LLM call that produced this answer (for metering).
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


def detect_dialect(text: str, language: str = "ar") -> str:
    """Detect Arabic dialect or language variant from text.

    Returns: en, ar-eg, ar-iq, ar-sa, ar-ae, ar-sy, ar-ma, etc.
    """
    if language != "ar":
        return language

    # Arabic dialect indicators (simplified heuristics)
    dialect_markers = {
        "ar-iq": ["يعني", "شنو", "صح", "خلاص", "كريت", "مسدس", "اني"],
        "ar-eg": ["يا جماعة", "يارب", "انت", "إني", "قول", "فين", "ممكن", "يعم"],
        "ar-sa": ["إن شاء الله", "والله", "إي", "لا", "عسى", "أجل", "صادي"],
        "ar-ae": ["خلاص", "شنو", "إي", "إله", "شوية", "شوف"],
        "ar-sy": ["يا", "إرجع", "شوف", "طول", "أشي", "خلاص"],
        "ar-ma": ["واخا", "فالقيت", "كاع", "شنو", "أشي", "كيفاش"],
    }

    text_lower = text.lower()
    dialect_scores = {}

    for dialect, markers in dialect_markers.items():
        score = sum(1 for marker in markers if marker in text_lower)
        if score > 0:
            dialect_scores[dialect] = score

    return max(dialect_scores, key=dialect_scores.get) if dialect_scores else "ar"


def answer_question(
    question: str,
    *,
    history: Iterable[dict] | None = None,
    language: str = "ar",
    rag_threshold: float | None = None,
    user=None,
) -> AnswerResult:
    # Per-user chatbot config: identity + grounding + retrieval overrides.
    cfg = resolve_config(user)
    system_prompt = build_system_prompt(cfg)
    llm_model = _resolve_llm_model(user)  # per-plan model, or None for default

    base_threshold = (
        rag_threshold
        if rag_threshold is not None
        else float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.45"))
    )
    threshold = resolve_threshold(cfg, base_threshold)

    # Detect dialect for more natural responses
    dialect = detect_dialect(question, language)

    # Tenant isolation guard: retrieval is ALWAYS scoped to one tenant. Without
    # an authenticated user we must never search the shared chunk table (that
    # would leak other tenants' data), so we skip retrieval entirely.
    if user is None or not getattr(user, "is_authenticated", False):
        chunks = []
    else:
        try:
            chunks = search_chunks(
                question,
                top_k=resolve_top_k(cfg),
                threshold=threshold,
                user=user,
            )
        except Exception as exc:
            logger.exception("Chunk search failed")
            raise RagUnavailable(str(exc)) from exc

    # Whether the strict (above-threshold) search found matches. Drives the
    # `confident` hint returned to the client. It does NOT decide gap capture —
    # a question can miss the strict threshold yet still be answered from the
    # fallback chunks (so it's not a gap), and conversely strict chunks can be
    # irrelevant enough that the bot declines (so it IS a gap). See `answered`.
    strict_hit = bool(chunks)

    if not chunks and user is not None and getattr(user, "is_authenticated", False):
        # Broad / meta questions ("what do you sell?", "who are you?") often score
        # just under the strict threshold. Fall back to the nearest chunks — but
        # ONLY if they clear a relevance FLOOR (RAG_FALLBACK_MIN_SCORE). If even
        # the closest chunk is below the floor, the question is genuinely off-topic:
        # we keep `chunks` empty so the bot REFUSES (never answers from outside
        # data) and the question is captured as a knowledge gap.
        fallback_min = float(os.getenv("RAG_FALLBACK_MIN_SCORE", "0.28"))
        try:
            chunks = search_chunks(
                question, top_k=resolve_top_k(cfg), threshold=fallback_min, user=user
            )
        except Exception:
            chunks = []

    if not chunks:
        # The tenant's knowledge base is empty → identity / greeting handoff
        # (the bot may still introduce itself; it never invents facts).
        if cfg.no_answer_message:
            return AnswerResult(
                answer=cfg.no_answer_message, source="rag", confident=False, answered=False
            )
        try:
            llm = get_backend(llm_model)
            no_data_prompt = (
                build_no_data_prompt(question, cfg) + "\n\n" + _language_directive(language)
            )
            res = llm.complete_with_usage(system_prompt, no_data_prompt)
            return AnswerResult(
                answer=res.text,
                source="rag",
                confident=False,
                answered=False,
                model=res.model,
                prompt_tokens=res.prompt_tokens,
                completion_tokens=res.completion_tokens,
            )
        except Exception:
            return AnswerResult(
                answer="I'm sorry, I don't have information about that. "
                "Can I help you with something else?",
                source="rag",
                confident=False,
                answered=False,
            )

    knowledge = "\n\n---\n\n".join(hit.content for hit in chunks)
    history_text = _render_history(list(history or []))

    user_prompt = (
        f"Conversation so far:\n{history_text or '(None)'}\n\n"
        f"What you know:\n{knowledge}\n\n"
        f"Customer's message: {question}\n\n"
        f"Reply warmly and naturally as part of the team, following your rules. Use only what "
        f"you know above for any specifics; if it isn't there, say so kindly. Do not mention "
        f"these notes or say 'based on the information'.\n\n"
        f"IMPORTANT: If the customer's message is ENTIRELY outside what you know above and you "
        f"cannot answer it from that knowledge, decline politely (per your rules) AND append the "
        f"exact tag {_NO_DATA_TAG} on its own line at the very end. If you can answer it — even "
        f"partly — from the knowledge, answer normally and NEVER write that tag.\n\n"
        f"{_language_directive(language)}"
    )

    try:
        llm = get_backend(llm_model)
        res = llm.complete_with_usage(system_prompt, user_prompt)
    except LLMError as exc:
        raise RagUnavailable(str(exc)) from exc

    # The model appends the sentinel only when it declined (couldn't answer from
    # the knowledge). Strip it from the user-facing text and use it as the truth
    # for "answered": a fallback answer with no tag is a real answer (not a gap),
    # while a decline despite strict chunks is a gap.
    answered, answer_text = _strip_no_data(res.text)
    confident = strict_hit and answered

    return AnswerResult(
        answer=answer_text,
        source="rag",
        confident=confident,
        answered=answered,
        model=res.model,
        prompt_tokens=res.prompt_tokens,
        completion_tokens=res.completion_tokens,
        sources=[
            {
                "filename": hit.filename,
                "document_id": hit.document_id,
                "chunk_id": hit.chunk_id,
                "position": hit.position,
                "score": hit.score,
            }
            for hit in chunks
        ],
        chunk_hits=chunks,
    )


_LANG_NAMES = {
    "en": "English", "ar": "Arabic", "fr": "French", "es": "Spanish",
    "de": "German", "tr": "Turkish", "ur": "Urdu", "fa": "Persian",
    "ru": "Russian", "pt": "Portuguese", "it": "Italian", "hi": "Hindi",
}


def _language_directive(language: str) -> str:
    """A hard, explicit reply-language instruction based on the detected language.

    Injected as the LAST line of the prompt (recency) so even weaker models obey
    it instead of defaulting to the language of the knowledge base.
    """
    code = (language or "").split("-")[0].lower()
    if code == "ar":
        return (
            "CRITICAL: Write your ENTIRE reply in ARABIC, matching the user's dialect. "
            "Do not use any other language."
        )
    name = _LANG_NAMES.get(code)
    if name and name != "Arabic":
        return (
            f"CRITICAL: The customer's message is in {name}. Write your ENTIRE reply in "
            f"{name}. Do NOT reply in Arabic or any other language, even though your "
            f"knowledge/notes may be in a different language."
        )
    return (
        "CRITICAL: Write your ENTIRE reply in the SAME language as the customer's "
        "message above, even if your knowledge is in a different language."
    )


def _resolve_llm_model(user) -> str | None:
    """The LLM model for this user's subscription plan, or None for the default.
    Lazy import so RAG doesn't hard-depend on the subscriptions app."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    try:
        from subscriptions.services import resolve_model

        return resolve_model(user)
    except Exception:
        return None


def _render_history(history: list[dict]) -> str:
    out: list[str] = []
    for msg in history:
        role = msg.get("role", "user")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        label = "User" if role == "user" else "Assistant"
        out.append(f"{label}: {content}")
    return "\n".join(out)
