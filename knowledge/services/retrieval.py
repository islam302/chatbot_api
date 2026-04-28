"""Search across the Q&A bank and document chunks.

Embeddings are stored as JSON on each row, and similarity is computed in
Python with numpy. This is fast enough for tens of thousands of rows; once
you outgrow that, swap the implementation in :func:`_topk_by_cosine` for
a pgvector ``<=>`` lookup — the public API stays identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
from django.db.models import QuerySet

from ..models import DocumentChunk, QuestionAnswer
from .embeddings import embed_one


@dataclass
class QAHit:
    id: str
    question: str
    answer: str
    score: float


@dataclass
class ChunkHit:
    chunk_id: str
    document_id: str
    filename: str
    content: str
    position: int
    score: float


def search_qa_bank(query: str, *, top_k: int = 5, threshold: float = 0.78) -> list[QAHit]:
    """Return the top Q&A matches above ``threshold`` cosine similarity."""
    vector, _ = embed_one(query)

    candidates = list(
        QuestionAnswer.objects.filter(is_active=True).exclude(embedding=None)
    )
    if not candidates:
        return []

    matrix = np.array([obj.embedding for obj in candidates], dtype=np.float32)
    scores = _cosine_similarity(np.asarray(vector, dtype=np.float32), matrix)

    ranked = sorted(zip(candidates, scores), key=lambda item: item[1], reverse=True)
    hits: list[QAHit] = []
    for obj, score in ranked[:top_k]:
        if score < threshold:
            break
        hits.append(
            QAHit(
                id=str(obj.id),
                question=obj.question,
                answer=obj.answer,
                score=float(score),
            )
        )
    return hits


def search_chunks(query: str, *, top_k: int = 6, threshold: float = 0.5) -> list[ChunkHit]:
    """Return the top document chunks above ``threshold`` cosine similarity."""
    vector, _ = embed_one(query)

    chunks: QuerySet[DocumentChunk] = (
        DocumentChunk.objects.select_related("document")
        .filter(document__is_active=True)
        .exclude(embedding=None)
    )
    pool = list(chunks)
    if not pool:
        return []

    matrix = np.array([c.embedding for c in pool], dtype=np.float32)
    scores = _cosine_similarity(np.asarray(vector, dtype=np.float32), matrix)
    order = np.argsort(-scores)

    hits: list[ChunkHit] = []
    for idx in order[:top_k]:
        score = float(scores[idx])
        if score < threshold:
            break
        chunk = pool[idx]
        hits.append(
            ChunkHit(
                chunk_id=str(chunk.id),
                document_id=str(chunk.document_id),
                filename=chunk.document.filename,
                content=chunk.content,
                position=chunk.position,
                score=score,
            )
        )
    return hits


def _cosine_similarity(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    query_norm = np.linalg.norm(query) or 1.0
    matrix_norms = np.linalg.norm(matrix, axis=1)
    matrix_norms[matrix_norms == 0] = 1.0
    return (matrix @ query) / (matrix_norms * query_norm)
