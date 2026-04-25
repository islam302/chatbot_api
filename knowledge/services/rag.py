"""High-level "answer a question" pipeline.

Order of operations (all configurable):

1. **Q&A bank** — semantic match against ``FixedQuestion`` and ``QuestionAnswer``.
   If the top hit is above ``QA_BANK_THRESHOLD``, return the curated answer
   directly. This is the single highest-quality signal: it's an answer
   you've already vetted.

2. **RAG over documents** — semantic search over ``DocumentChunk`` rows.
   The top chunks are stitched into a context window and passed to the
   configured LLM with a strict "answer only from context" system prompt.

3. **Fallback** — if neither produces a confident result, return a polite
   "I don't know" response and record the question as ``UnansweredQuestion``
   so it can be triaged later.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Iterable

from ..models import UnansweredQuestion
from .llm import LLMError, get_backend
from .retrieval import ChunkHit, QAHit, search_chunks, search_qa_bank

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a careful assistant grounded in the provided context.

Rules:
- Answer ONLY using information from the "Context" section.
- If the answer is not in the context, reply that you do not have enough information and suggest the user contact a human.
- Quote URLs and identifiers verbatim.
- Reply in the same language as the question.
- Keep answers concise and structured.
"""


class RagUnavailable(RuntimeError):
    """Raised when the answer pipeline cannot run (missing key, missing deps)."""


@dataclass
class AnswerResult:
    answer: str
    source: str  # "qa_bank" | "rag" | "fallback"
    source_id: str = ""
    sources: list[dict] = field(default_factory=list)
    qa_hits: list[QAHit] = field(default_factory=list)
    chunk_hits: list[ChunkHit] = field(default_factory=list)
    confident: bool = True


def answer_question(
    question: str,
    *,
    history: Iterable[dict] | None = None,
    qa_threshold: float | None = None,
    rag_threshold: float | None = None,
    log_unanswered: bool = True,
) -> AnswerResult:
    qa_threshold = (
        qa_threshold
        if qa_threshold is not None
        else float(os.getenv("QA_BANK_THRESHOLD", "0.82"))
    )
    rag_threshold = (
        rag_threshold
        if rag_threshold is not None
        else float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.45"))
    )

    # 1) Q&A bank
    try:
        qa_hits = search_qa_bank(question, top_k=3, threshold=qa_threshold)
    except Exception:
        logger.exception("Q&A bank search failed; continuing with RAG")
        qa_hits = []

    if qa_hits:
        top = qa_hits[0]
        return AnswerResult(
            answer=top.answer,
            source="qa_bank",
            source_id=top.id,
            sources=[
                {"type": h.source, "id": h.id, "question": h.question, "score": h.score}
                for h in qa_hits
            ],
            qa_hits=qa_hits,
        )

    # 2) RAG over document chunks
    try:
        chunks = search_chunks(question, top_k=6, threshold=rag_threshold)
    except Exception as exc:
        logger.exception("Chunk search failed")
        raise RagUnavailable(str(exc)) from exc

    if not chunks:
        return _fallback(question, log_unanswered=log_unanswered)

    context = "\n\n---\n\n".join(
        f"[Source: {hit.filename}#{hit.position}]\n{hit.content}" for hit in chunks
    )
    history_text = _render_history(list(history or []))

    user_prompt = (
        f"Conversation so far:\n{history_text or '(none)'}\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {question}"
    )

    try:
        llm = get_backend()
        answer_text = llm.complete(SYSTEM_PROMPT, user_prompt)
    except LLMError as exc:
        raise RagUnavailable(str(exc)) from exc

    return AnswerResult(
        answer=answer_text,
        source="rag",
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


def _fallback(question: str, *, log_unanswered: bool) -> AnswerResult:
    if log_unanswered:
        UnansweredQuestion.objects.get_or_create(question=question)
    return AnswerResult(
        answer=(
            "I don't have enough information to answer that yet. "
            "I've logged the question so it can be reviewed."
        ),
        source="fallback",
        confident=False,
    )


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
