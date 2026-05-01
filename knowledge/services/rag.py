"""RAG-only answer pipeline with multilingual and multi-dialect support.

Retrieves relevant document chunks and passes them to an LLM
for question answering in the user's language and dialect.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Iterable

from .llm import LLMError, get_backend
from .retrieval import ChunkHit, search_chunks

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """أنت مساعد ذكي ولطيف وودود تجيب على الأسئلة بناءً على المعلومات المتوفرة.

القواعد الأساسية:
- اجب فقط من المعلومات الموجودة في قسم "السياق" (Context).
- لو المعلومة ما موجودة، قول بأدب واضح إنك ما عندك معلومات كافية.
- اجب باللغة واللهجة نفسها اللي السؤال اتسأل فيها.
- كن مرن في الجواب لكن قريب من الأسلوب الطبيعي والودود.
- اذا الشخص استخدم لهجة معينة (عراقي، مصري، خليجي، شامي، وغيره)، استخدم نفس اللهجة في الرد.
- كن موجز وواضح ومنظم في الرد.
- اذا في معلومات متعددة، رتبها بطريقة سهلة وسلسة.
- اجب بأسلوب شخصي لطيف ما يكون رسمي جداً، بس احترافي وموثوق.

Remember:
- Be empathetic and kind in your tone.
- Match the user's language (Arabic dialects, English, etc).
- Use natural, conversational language.
- Be flexible but maintain accuracy.
- When uncertain, acknowledge it honestly."""


class RagUnavailable(RuntimeError):
    """Raised when RAG pipeline cannot run."""


@dataclass
class AnswerResult:
    answer: str
    source: str  # "rag"
    source_id: str = ""
    sources: list[dict] = field(default_factory=list)
    chunk_hits: list[ChunkHit] = field(default_factory=list)
    confident: bool = True


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
    rag_threshold = (
        rag_threshold
        if rag_threshold is not None
        else float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.45"))
    )

    # Detect dialect for more natural responses
    dialect = detect_dialect(question, language)

    try:
        chunks = search_chunks(question, top_k=6, threshold=rag_threshold, user=user)
    except Exception as exc:
        logger.exception("Chunk search failed")
        raise RagUnavailable(str(exc)) from exc

    if not chunks:
        no_info_messages = {
            "ar": "للأسف ما عندي معلومات كافية عشان أجاوب على سؤالك. ممكن تعيد صيغة السؤال؟",
            "ar-iq": "يعني ما عندي معلومات كافية، تعيد السؤال بطريقة ثانية؟",
            "ar-eg": "آسف يعني ما عندي معلومات كفاية. تقول السؤال بطريقة تانية؟",
            "ar-sa": "للأسف ما فيه معلومات، جرب تسأل بطريقة ثانية إن شاء الله.",
            "en": "I don't have enough information to answer that question. Could you rephrase it?",
        }
        answer = no_info_messages.get(dialect, no_info_messages.get(language, no_info_messages["ar"]))
        return AnswerResult(answer=answer, source="rag", confident=False)

    context = "\n\n---\n\n".join(
        f"[المصدر/Source: {hit.filename}#{hit.position}]\n{hit.content}" for hit in chunks
    )
    history_text = _render_history(list(history or []))

    # Build a dialect-aware prompt
    dialect_instructions = ""
    if language == "ar":
        if dialect.startswith("ar-"):
            dialect_code = dialect.split("-")[1]
            dialect_instruction_map = {
                "iq": "استخدم اللهجة العراقية الطبيعية والودية.",
                "eg": "استخدم اللهجة المصرية الطبيعية والودية.",
                "sa": "استخدم اللهجة الخليجية الطبيعية والودية.",
                "ae": "استخدم اللهجة الإماراتية الطبيعية والودية.",
                "sy": "استخدم اللهجة الشامية الطبيعية والودية.",
                "ma": "استخدم اللهجة المغربية الطبيعية والودية.",
            }
            dialect_instructions = dialect_instruction_map.get(dialect_code, "استخدم اللهجة العربية الطبيعية والودية.")
        else:
            dialect_instructions = "استخدم الفصحى السهلة والودية المناسبة للجميع."

    user_prompt = (
        f"المحادثة حتى الآن/Conversation so far:\n{history_text or '(لا توجد/None)'}\n\n"
        f"السياق/Context:\n{context}\n\n"
        f"السؤال/Question: {question}\n\n"
        f"التعليمات/Instructions:\n"
        f"- {dialect_instructions}\n"
        f"- أجب بطريقة طبيعية ولطيفة ومرنة.\n"
        f"- كن موجز وواضح.\n"
        f"- استخدم نفس اللغة/اللهجة في الرد."
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
