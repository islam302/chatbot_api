"""Process API content (articles, docs, rows, ...) into RAG-queryable chunks.

Supports **incremental sync**: on re-sync only new or changed source records are
re-embedded, unchanged records are left untouched, and records that disappeared
from the source are removed. This keeps syncing a live/updating database cheap
(both in time and embedding cost) instead of re-embedding everything every time.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from django.db import transaction
from django.db.models import Max

from ..models import DocumentChunk, DocumentStatus, SourceType, UploadedDocument
from .chunking import _chunk, _embed_in_batches

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200


class APIContentProcessingError(Exception):
    """Raised when API content processing fails."""


@dataclass
class SyncStats:
    processed: int = 0       # source items with usable content
    items_new: int = 0       # items embedded for the first time
    items_updated: int = 0   # items whose content changed and were re-embedded
    items_unchanged: int = 0 # items skipped (no change → no embedding cost)
    items_removed: int = 0   # items deleted because they vanished from the source
    chunks_created: int = 0  # chunks newly written this sync
    errors: int = 0

    def as_dict(self) -> dict:
        return {
            "processed": self.processed,
            "chunks_created": self.chunks_created,
            "items_new": self.items_new,
            "items_updated": self.items_updated,
            "items_unchanged": self.items_unchanged,
            "items_removed": self.items_removed,
            "errors": self.errors,
        }


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _default_source_id(item: dict, content: str) -> str:
    """Pick a stable id for a source item, falling back to a content hash."""
    if isinstance(item, dict):
        for key in ("id", "pk", "uuid", "slug", "_id"):
            value = item.get(key)
            if value not in (None, ""):
                return str(value)[:255]
    # No natural id → hash of content. Stable while the content is stable.
    return _hash_text(content)[:255]


class APIContentRAGProcessor:
    """Convert API content (articles, docs, rows, ...) to RAG-queryable chunks."""

    def __init__(self, document_name: str = "API Content", user=None, api_url: str = "", items_key: str = "results"):
        self.document_name = document_name
        self.user = user
        self.api_url = api_url
        self.items_key = items_key
        self._ensure_api_document()

    def _ensure_api_document(self):
        """Create (or fetch) a virtual document that owns the API content's chunks."""
        query_params = {"filename": self.document_name}
        if self.user:
            query_params["uploaded_by"] = self.user

        defaults = {
            "file_size": 0,
            "processing_status": DocumentStatus.COMPLETED,
            "source_type": SourceType.API,
            "api_url": self.api_url,
            "items_key": self.items_key,
        }
        if self.user:
            defaults["uploaded_by"] = self.user

        doc, created = UploadedDocument.objects.get_or_create(
            **query_params,
            defaults=defaults,
        )
        self.api_document = doc

        if not created and self.api_url:
            doc.source_type = SourceType.API
            doc.api_url = self.api_url
            doc.items_key = self.items_key
            doc.save()

        if created:
            logger.info("Created virtual document: %s", self.document_name)

    def process_items(
        self,
        items: list[dict],
        extract_text_fn: Optional[Callable[[dict], str]] = None,
        id_fn: Optional[Callable[[dict], str]] = None,
        *,
        full_refresh: bool = False,
    ) -> dict:
        """Incrementally sync a list of source items into RAG chunks.

        Args:
            items: source records (dicts) from the API/DB.
            extract_text_fn: item -> text. Defaults to pretty JSON.
            id_fn: item -> stable id. Defaults to a natural id key or content hash.
            full_refresh: if True, wipe and rebuild everything (legacy behaviour).

        Returns: stats dict (see ``SyncStats``).
        """
        extract_text_fn = extract_text_fn or (
            lambda item: json.dumps(item, ensure_ascii=False, indent=2)
        )
        id_fn = id_fn or (lambda item: "")

        stats = SyncStats()

        try:
            # --- Build the incoming view: source_id -> (content, hash) -----------
            incoming: dict[str, tuple[str, str]] = {}
            for item in items:
                try:
                    content = extract_text_fn(item)
                    if not content or not content.strip():
                        continue
                    sid = (id_fn(item) or "").strip() or _default_source_id(item, content)
                    incoming[sid] = (content, _hash_text(content))
                    stats.processed += 1
                except Exception as exc:  # noqa: BLE001 - per-item isolation
                    logger.error("Error reading API item: %s", exc)
                    stats.errors += 1

            # --- Existing state in DB: source_id -> stored hash -----------------
            if full_refresh:
                existing_hash: dict[str, str] = {}
                DocumentChunk.objects.filter(document=self.api_document).delete()
            else:
                existing_hash = self._existing_hashes()

            incoming_ids = set(incoming)
            existing_ids = set(existing_hash)

            changed_ids = {
                sid for sid in incoming_ids
                if existing_hash.get(sid) != incoming[sid][1]
            }
            removed_ids = existing_ids - incoming_ids
            unchanged_ids = incoming_ids - changed_ids

            stats.items_unchanged = len(unchanged_ids)
            stats.items_new = len([s for s in changed_ids if s not in existing_ids])
            stats.items_updated = len([s for s in changed_ids if s in existing_ids])
            stats.items_removed = len(removed_ids)

            if not changed_ids and not removed_ids:
                logger.info("API sync: nothing changed for %s", self.document_name)
                return stats.as_dict()

            # --- Chunk only the changed/new items, then embed in one batch ------
            pending: list[tuple[str, str, str]] = []  # (source_id, hash, piece)
            for sid in changed_ids:
                content, chash = incoming[sid]
                for piece in _chunk(content, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
                    pending.append((sid, chash, piece))

            embeddings: list = []
            model = ""
            if pending:
                try:
                    embeddings, model = _embed_in_batches([p[2] for p in pending])
                except Exception as exc:  # noqa: BLE001
                    raise APIContentProcessingError(f"Embedding failed: {exc}")

            # --- Persist: delete stale, insert fresh ----------------------------
            with transaction.atomic():
                stale_ids = changed_ids | removed_ids
                if stale_ids:
                    DocumentChunk.objects.filter(
                        document=self.api_document, source_id__in=stale_ids
                    ).delete()

                next_pos = self._next_position()
                new_chunks = [
                    DocumentChunk(
                        document=self.api_document,
                        position=next_pos + idx,
                        content=piece,
                        embedding=vector,
                        embedding_model=model,
                        source_id=sid,
                        content_hash=chash,
                        metadata={"source": "api", "source_id": sid},
                    )
                    for idx, ((sid, chash, piece), vector) in enumerate(
                        zip(pending, embeddings)
                    )
                ]
                if new_chunks:
                    DocumentChunk.objects.bulk_create(new_chunks)
                stats.chunks_created = len(new_chunks)

            logger.info("API sync completed for %s: %s", self.document_name, stats.as_dict())
            return stats.as_dict()

        except APIContentProcessingError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error processing API content: %s", exc)
            raise APIContentProcessingError(str(exc))

    # ------------------------------------------------------------------ helpers
    def _existing_hashes(self) -> dict[str, str]:
        """Map each existing source_id to its stored content hash."""
        rows = (
            DocumentChunk.objects.filter(document=self.api_document)
            .exclude(source_id="")
            .values_list("source_id", "content_hash")
        )
        # All chunks of one item share the same hash; last write wins.
        return {sid: chash for sid, chash in rows}

    def _next_position(self) -> int:
        current_max = (
            DocumentChunk.objects.filter(document=self.api_document)
            .aggregate(m=Max("position"))
            .get("m")
        )
        return (current_max + 1) if current_max is not None else 0
