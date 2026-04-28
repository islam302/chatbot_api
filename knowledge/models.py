import uuid

from django.conf import settings
from django.db import models


class AnswerType(models.TextChoices):
    SINGLE = "single", "Single answer"
    MULTIPLE = "multiple", "Multiple answers"


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class EmbeddingMixin(models.Model):
    """Stores a vector embedding plus the model that produced it.

    Stored as JSON for portability — works on SQLite today and is trivially
    swappable for pgvector later (see services/retrieval.py).
    """

    embedding = models.JSONField(null=True, blank=True)
    embedding_model = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        abstract = True


class QuestionAnswer(TimestampedModel, EmbeddingMixin):
    """A curated Q&A pair. The chat pipeline searches these first."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.TextField(max_length=512)
    answer = models.TextField(max_length=4096)
    answer_type = models.CharField(
        max_length=10, choices=AnswerType.choices, default=AnswerType.SINGLE
    )
    overview_description = models.TextField(max_length=5000, blank=True, default="")
    count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="questions",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["is_active", "-count"])]

    def __str__(self):
        return self.question[:50]

    def increment_count(self):
        self.count = models.F("count") + 1
        self.save(update_fields=["count"])
        self.refresh_from_db(fields=["count"])


class AvailableLanguage(TimestampedModel):
    code = models.CharField(max_length=8, primary_key=True)
    name = models.CharField(max_length=50)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class SimpleQuestionTree(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=300)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    answer = models.TextField(blank=True, default="")
    images = models.JSONField(default=list, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    language = models.CharField(max_length=8, default="ar")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tree_nodes",
    )

    class Meta:
        ordering = ["order", "created_at"]
        indexes = [
            models.Index(fields=["language", "is_active"]),
            models.Index(fields=["parent", "order"]),
        ]

    def __str__(self):
        return self.title

    def is_root(self):
        return self.parent_id is None


class DocumentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


def upload_document_path(instance, filename):
    return f"documents/{instance.id}/{filename}"


class UploadedDocument(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(upload_to=upload_document_path)
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
    QA_BANK = "qa_bank", "Q&A bank"
    RAG = "rag", "RAG"
    FALLBACK = "fallback", "Fallback"


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
