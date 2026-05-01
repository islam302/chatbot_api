"""Search document chunks for RAG retrieval.

Embeddings are stored as JSON and similarity is computed in Python with numpy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from django.db.models import QuerySet

from ..models import DocumentChunk
from .embeddings import embed_one


@dataclass
class ChunkHit:
    chunk_id: str
    document_id: str
    filename: str
    content: str
    position: int
    score: float


def search_chunks(query: str, *, top_k: int = 6, threshold: float = 0.5, user=None) -> list[ChunkHit]:
    """Return the top document chunks above ``threshold`` cosine similarity.

    If user is provided, only returns chunks from that user's documents.
    """
    vector, _ = embed_one(query)

    chunks_qs = (
        DocumentChunk.objects.select_related("document")
        .filter(document__is_active=True)
        .exclude(embedding=None)
    )

    if user:
        chunks_qs = chunks_qs.filter(document__uploaded_by=user)

    chunks: QuerySet[DocumentChunk] = chunks_qs
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
