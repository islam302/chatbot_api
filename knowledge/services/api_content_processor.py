"""Process API content (articles, docs, etc.) into RAG-queryable chunks."""

import json
import logging
from typing import Callable, Optional

from django.db import transaction

from ..models import DocumentChunk, UploadedDocument, DocumentStatus, SourceType
from .chunking import _chunk, _embed_in_batches

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200


class APIContentProcessingError(Exception):
    """Raised when API content processing fails."""


class APIContentRAGProcessor:
    """Convert API content (articles, docs, etc.) to RAG-queryable chunks."""

    def __init__(self, document_name: str = "API Content", user=None, api_url: str = "", items_key: str = "results"):
        """
        Initialize processor.

        Args:
            document_name: Virtual document name for storing chunks
            user: User who owns this API content (for multi-tenancy)
            api_url: Source API URL
            items_key: JSON key containing items
        """
        self.document_name = document_name
        self.user = user
        self.api_url = api_url
        self.items_key = items_key
        self._ensure_api_document()

    def _ensure_api_document(self):
        """Create a virtual document for API content if it doesn't exist."""
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

        # Update existing document with latest API info
        if not created and self.api_url:
            doc.source_type = SourceType.API
            doc.api_url = self.api_url
            doc.items_key = self.items_key
            doc.save()

        if created:
            logger.info(f"Created virtual document: {self.document_name}")

    def process_items(
        self,
        items: list[dict],
        extract_text_fn: Optional[Callable[[dict], str]] = None,
    ) -> dict:
        """
        Process a list of API items into RAG chunks.

        Args:
            items: List of dict items from API
            extract_text_fn: Function to extract/format text from each item.
                           If None, converts to formatted JSON.
                           Should return str or None.

        Returns:
            Dict with stats: {processed, chunks_created, errors}
        """
        if extract_text_fn is None:
            extract_text_fn = lambda item: json.dumps(item, ensure_ascii=False, indent=2)

        stats = {"processed": 0, "chunks_created": 0, "errors": 0}

        try:
            # Clear old chunks
            DocumentChunk.objects.filter(document=self.api_document).delete()

            all_chunks = []

            for item in items:
                try:
                    content = extract_text_fn(item)
                    if not content or not content.strip():
                        continue

                    # Split into overlapping chunks
                    pieces = _chunk(content, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
                    all_chunks.extend(pieces)
                    stats["processed"] += 1

                except Exception as e:
                    logger.error(f"Error processing API item: {e}")
                    stats["errors"] += 1

            if not all_chunks:
                logger.warning(f"No chunks extracted from {len(items)} items")
                return stats

            # Generate embeddings in batches
            try:
                embeddings, model = _embed_in_batches(all_chunks)
            except Exception as e:
                raise APIContentProcessingError(f"Embedding failed: {e}")

            # Store chunks with embeddings
            with transaction.atomic():
                chunks = [
                    DocumentChunk(
                        document=self.api_document,
                        position=idx,
                        content=piece,
                        embedding=vector,
                        embedding_model=model,
                        metadata={"source": "api"},
                    )
                    for idx, (piece, vector) in enumerate(zip(all_chunks, embeddings))
                ]
                DocumentChunk.objects.bulk_create(chunks)
                stats["chunks_created"] = len(chunks)

            logger.info(f"API content processing completed: {stats}")
            return stats

        except APIContentProcessingError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error processing API content: {e}")
            raise APIContentProcessingError(str(e))
