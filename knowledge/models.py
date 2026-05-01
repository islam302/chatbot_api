import binascii
import os
import uuid

from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db import models


class User(AbstractUser):
    """Custom User model with UUID primary key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        db_table = "auth_user"


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class EmbeddingMixin(models.Model):
    """Stores vector embeddings for RAG retrieval.

    Stored as JSON for portability — works on SQLite and is swappable
    for pgvector later (see services/retrieval.py).
    """

    embedding = models.JSONField(null=True, blank=True)
    embedding_model = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        abstract = True


class DocumentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


def upload_document_path(instance, filename):
    return f"documents/{instance.id}/{filename}"


class SourceType(models.TextChoices):
    FILE = "file", "File Upload"
    API = "api", "API Sync"


class UploadedDocument(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(upload_to=upload_document_path, null=True, blank=True)
    filename = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(default=0)
    processing_status = models.CharField(
        max_length=20,
        choices=DocumentStatus.choices,
        default=DocumentStatus.PENDING,
    )
    error_message = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_documents",
    )
    source_type = models.CharField(
        max_length=10,
        choices=SourceType.choices,
        default=SourceType.FILE,
    )
    api_url = models.URLField(blank=True, default="", help_text="Source API URL (if synced from API)")
    items_key = models.CharField(max_length=100, blank=True, default="", help_text="JSON key containing items")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["processing_status", "is_active"])]

    def __str__(self):
        return self.filename

    @property
    def file_size_mb(self):
        return round(self.file_size / (1024 * 1024), 2) if self.file_size else 0


class DocumentChunk(TimestampedModel, EmbeddingMixin):
    """A persisted chunk of a parsed document, ready for retrieval."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        UploadedDocument, on_delete=models.CASCADE, related_name="chunks"
    )
    position = models.PositiveIntegerField(default=0)
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["document_id", "position"]
        indexes = [models.Index(fields=["document", "position"])]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "position"], name="unique_chunk_position"
            )
        ]

    def __str__(self):
        return f"{self.document_id}#{self.position}"


class FeedbackRating(models.TextChoices):
    UP = "up", "Helpful"
    DOWN = "down", "Not helpful"


class AnswerSource(models.TextChoices):
    RAG = "rag", "RAG"


class ChatFeedback(TimestampedModel):
    """User feedback on a chat answer — feeds quality monitoring + retraining."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.TextField()
    answer = models.TextField()
    source = models.CharField(
        max_length=16,
        choices=AnswerSource.choices,
        default=AnswerSource.RAG,
    )
    source_id = models.CharField(max_length=64, blank=True, default="")
    rating = models.CharField(max_length=8, choices=FeedbackRating.choices)
    comment = models.TextField(blank=True, default="")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_feedback",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["rating", "source"])]

    def __str__(self):
        return f"{self.rating} {self.question[:40]}"


class APIKey(TimestampedModel):
    """Per-user API key for authentication and multi-tenancy."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_key",
    )
    key = models.CharField(max_length=40, unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["key", "is_active"])]

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
        return super().save(*args, **kwargs)

    @staticmethod
    def generate_key():
        return binascii.hexlify(os.urandom(20)).decode()

    def __str__(self):
        return f"{self.user.username} - {self.key[:8]}..."
