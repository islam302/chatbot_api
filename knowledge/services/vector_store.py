"""Swappable vector-search backends for RAG retrieval.

Two backends:

* ``numpy``  - default; works on any database (incl. SQLite). Scans candidate
  rows in batches and keeps only the top ``fetch_k`` by cosine similarity, so
  peak memory is bounded by the batch size, not the whole table.
* ``pgvector`` - optional; uses a Postgres ``vector`` column + ANN index so the
  database does the nearest-neighbour search. Activated with
  ``RAG_VECTOR_BACKEND=pgvector`` once the column/index exist (see
  ``docs``/management command). Falls back to numpy if unavailable.

Both return ``Candidate`` objects that carry the chunk's embedding so the
retrieval layer can re-rank (e.g. MMR) without a second DB round-trip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from django.conf import settings
from django.db import connection

from ..models import DocumentChunk

logger = logging.getLogger(__name__)


@dataclass
class Candidate:
    chunk_id: str
    score: float
    embedding: np.ndarray


def _base_queryset(*, user=None, document_ids=None):
    qs = (
        DocumentChunk.objects.filter(document__is_active=True)
        .exclude(embedding=None)
    )
    if user is not None:
        qs = qs.filter(document__uploaded_by=user)
    if document_ids:
        qs = qs.filter(document_id__in=document_ids)
    return qs


class NumpyVectorStore:
    """Brute-force cosine search, batched to keep memory bounded."""

    def search(self, query_vec, *, fetch_k, threshold, user=None, document_ids=None) -> list[Candidate]:
        q = np.asarray(query_vec, dtype=np.float32)
        q_norm = float(np.linalg.norm(q)) or 1.0

        batch_size = int(getattr(settings, "RAG_SCAN_BATCH", 2000)) or 2000
        qs = _base_queryset(user=user, document_ids=document_ids).order_by("id")

        kept: list[Candidate] = []
        scanned = 0
        ids: list = []
        embs: list = []

        def flush():
            nonlocal kept
            if not embs:
                return
            try:
                matrix = np.asarray(embs, dtype=np.float32)
            except ValueError:
                # Ragged embeddings (mixed dimensions) — score row by row.
                for cid, emb in zip(ids, embs):
                    vec = np.asarray(emb, dtype=np.float32)
                    if vec.shape != q.shape:
                        continue
                    score = float(vec @ q / ((np.linalg.norm(vec) or 1.0) * q_norm))
                    if score >= threshold:
                        kept.append(Candidate(str(cid), score, vec))
            else:
                if matrix.ndim != 2 or matrix.shape[1] != q.shape[0]:
                    return
                norms = np.linalg.norm(matrix, axis=1)
                norms[norms == 0] = 1.0
                scores = (matrix @ q) / (norms * q_norm)
                for i, score in enumerate(scores):
                    if score >= threshold:
                        kept.append(Candidate(str(ids[i]), float(score), matrix[i]))
            # Keep only the global top fetch_k so far.
            kept.sort(key=lambda c: -c.score)
            del kept[fetch_k:]
            ids.clear()
            embs.clear()

        for cid, emb in qs.values_list("id", "embedding").iterator(chunk_size=batch_size):
            if not emb:
                continue
            ids.append(cid)
            embs.append(emb)
            scanned += 1
            if len(ids) >= batch_size:
                flush()
        flush()

        if scanned:
            logger.debug("numpy vector search scanned %d chunks, kept %d", scanned, len(kept))
        return kept[:fetch_k]


class PgVectorStore:
    """Nearest-neighbour search via a Postgres pgvector column + index.

    Expects a column ``embedding_vec vector(N)`` on the chunk table, populated
    from ``embedding`` (see the ``setup_pgvector`` management command). Raises
    ``VectorBackendUnavailable`` if the column/extension is missing so the
    caller can fall back to numpy.
    """

    COLUMN = "embedding_vec"

    def search(self, query_vec, *, fetch_k, threshold, user=None, document_ids=None) -> list[Candidate]:
        if connection.vendor != "postgresql":
            raise VectorBackendUnavailable("pgvector backend requires PostgreSQL")

        table = DocumentChunk._meta.db_table
        doc_table = DocumentChunk._meta.get_field("document").related_model._meta.db_table
        vec_literal = "[" + ",".join(str(float(x)) for x in query_vec) + "]"

        where = [f"c.{self.COLUMN} IS NOT NULL", "d.is_active = TRUE"]
        params: list = [vec_literal]
        if user is not None:
            where.append("d.uploaded_by_id = %s")
            params.append(getattr(user, "pk", user))
        if document_ids:
            where.append("c.document_id = ANY(%s)")
            params.append(list(document_ids))

        # Cosine distance operator <=>; similarity = 1 - distance.
        sql = f"""
            SELECT c.id, c.{self.COLUMN} <=> %s::vector AS distance, c.embedding
            FROM {table} c
            JOIN {doc_table} d ON d.id = c.document_id
            WHERE {' AND '.join(where)}
            ORDER BY distance ASC
            LIMIT %s
        """
        params.append(fetch_k)

        try:
            with connection.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        except Exception as exc:  # column/extension missing, etc.
            raise VectorBackendUnavailable(str(exc)) from exc

        out: list[Candidate] = []
        for cid, distance, emb in rows:
            score = 1.0 - float(distance)
            if score < threshold:
                continue
            out.append(Candidate(str(cid), score, np.asarray(emb, dtype=np.float32)))
        return out


class VectorBackendUnavailable(RuntimeError):
    pass


def get_backend():
    name = getattr(settings, "RAG_VECTOR_BACKEND", "numpy")
    if name == "pgvector":
        return PgVectorStore()
    return NumpyVectorStore()


def search_candidates(query_vec, *, fetch_k, threshold, user=None, document_ids=None) -> list[Candidate]:
    """Run the configured backend, falling back to numpy on failure."""
    backend = get_backend()
    try:
        return backend.search(
            query_vec, fetch_k=fetch_k, threshold=threshold,
            user=user, document_ids=document_ids,
        )
    except VectorBackendUnavailable as exc:
        logger.warning("Vector backend unavailable (%s); falling back to numpy", exc)
        return NumpyVectorStore().search(
            query_vec, fetch_k=fetch_k, threshold=threshold,
            user=user, document_ids=document_ids,
        )
