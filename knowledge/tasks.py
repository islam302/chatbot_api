"""Celery tasks for the knowledge app."""

from __future__ import annotations

import logging

from celery import shared_task

from .models import UploadedDocument

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def ingest_document_task(self, document_id):
    """Parse, chunk and embed a document in the background.

    Status transitions (pending -> processing -> completed/failed) are persisted
    by ``ingest_document`` itself, so the API can poll the document for progress.
    """
    # Import here to avoid loading the embedding stack at worker import time.
    from .services.chunking import ingest_document

    try:
        document = UploadedDocument.objects.get(pk=document_id)
    except UploadedDocument.DoesNotExist:
        logger.error("ingest_document_task: document %s no longer exists", document_id)
        return None

    result = ingest_document(document)
    return {"document_id": str(document_id), "chunks_created": getattr(result, "chunks_created", 0)}


@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def capture_unanswered_task(self, user_id, question, language=""):
    """Filter + record a low-confidence chat question as a knowledge gap."""
    from django.contrib.auth import get_user_model

    from .services.unanswered import capture_unanswered

    try:
        user = get_user_model().objects.get(pk=user_id)
    except get_user_model().DoesNotExist:
        logger.error("capture_unanswered_task: user %s no longer exists", user_id)
        return None

    obj = capture_unanswered(user=user, question=question, language=language)
    return {"captured": bool(obj), "id": str(obj.pk) if obj else None}
