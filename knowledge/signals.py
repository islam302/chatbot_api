"""Auto-embed Q&A rows when their question/answer text changes.

Wraps the embedding call in a broad try/except so signal failures never
break the underlying CRUD operation. Rows that fail to embed remain
``embedding=NULL`` and can be backfilled later with ``manage.py embed_qa``.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import QuestionAnswer
from .services.embeddings import EmbeddingError, embed_one

logger = logging.getLogger(__name__)


@receiver(post_save, sender=QuestionAnswer)
def embed_qa_row(sender, instance, created, update_fields, **kwargs):
    if update_fields and "embedding" in update_fields:
        return  # avoid recursion on our own save below

    text = "\n".join(filter(None, [
        instance.question,
        getattr(instance, "overview_description", "") or "",
        instance.answer,
    ]))[:8000]

    try:
        vector, model = embed_one(text)
    except EmbeddingError as exc:
        logger.info("Skipping embedding for %s %s: %s", sender.__name__, instance.pk, exc)
        return
    except Exception:
        logger.exception("Unexpected embedding failure for %s %s", sender.__name__, instance.pk)
        return

    sender.objects.filter(pk=instance.pk).update(
        embedding=vector, embedding_model=model
    )
