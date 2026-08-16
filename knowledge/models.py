import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

# User and APIKey now live in the `Authentication` app.


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
    WEBSITE = "website", "Website Crawl"


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
    # Incremental sync: stable id of the source record (API item / row) and a
    # hash of that record's content, so re-syncs only re-embed what changed.
    source_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    content_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)

    class Meta:
        ordering = ["document_id", "position"]
        indexes = [
            models.Index(fields=["document", "position"]),
            models.Index(fields=["document", "source_id"]),
        ]
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


class BotTone(models.TextChoices):
    FRIENDLY = "friendly", "Friendly"
    FORMAL = "formal", "Formal"
    CONCISE = "concise", "Concise"


class ChatbotConfig(TimestampedModel):
    """Per-user chatbot identity + retrieval settings.

    Identity fields (name/company/language/tone) are injected into a
    system-controlled prompt; the strict grounding rules themselves are NOT
    user-editable, so every tenant's bot answers only from its own data.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chatbot_config",
    )
    assistant_name = models.CharField(max_length=100, blank=True, default="Assistant")
    company_name = models.CharField(max_length=150, blank=True, default="")
    # "auto" = reply in the user's language; otherwise a fallback language code.
    default_language = models.CharField(max_length=10, blank=True, default="auto")
    tone = models.CharField(max_length=20, choices=BotTone.choices, default=BotTone.FRIENDLY)
    # When True (default), the bot never answers outside the retrieved context.
    strict_grounding = models.BooleanField(default=True)
    # Optional static reply when nothing matches the user's data. Blank => the
    # LLM produces a localized "I don't have that" handoff (still grounded).
    no_answer_message = models.TextField(blank=True, default="")
    # Optional per-bot retrieval overrides (fall back to global settings if null).
    top_k = models.PositiveIntegerField(null=True, blank=True)
    similarity_threshold = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f"ChatbotConfig({self.user.username})"


class TenantQuota(TimestampedModel):
    """Per-tenant resource limits. A tenant is a User.

    Any field left null falls back to the global default in settings, so a row
    only needs to override what differs for that tenant. Enforced in
    services/quota.py at upload time (documents/size) and chat time (rate limit,
    monthly token cap).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quota",
    )
    max_documents = models.PositiveIntegerField(null=True, blank=True)
    max_total_mb = models.FloatField(null=True, blank=True)
    max_requests_per_min = models.PositiveIntegerField(null=True, blank=True)
    # Monthly input+output token budget. null/0 = unlimited.
    monthly_token_cap = models.PositiveBigIntegerField(null=True, blank=True)
    # Hard stop: a suspended tenant cannot upload or chat.
    is_suspended = models.BooleanField(default=False)

    def __str__(self):
        return f"TenantQuota({self.user.username})"


class UsageKind(models.TextChoices):
    CHAT = "chat", "Chat"
    EMBEDDING = "embedding", "Embedding"


class UsageRecord(TimestampedModel):
    """One metered event (a chat answer) for a tenant — feeds analytics/billing.

    Stores token counts, estimated cost, latency and retrieval confidence. The
    question text is intentionally NOT stored here (privacy); ChatFeedback keeps
    text when the user opts to rate an answer.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usage_records",
    )
    kind = models.CharField(max_length=16, choices=UsageKind.choices, default=UsageKind.CHAT)
    model = models.CharField(max_length=64, blank=True, default="")
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    cost_usd = models.FloatField(default=0.0)
    response_time_ms = models.PositiveIntegerField(default=0)
    confident = models.BooleanField(default=True)
    chunk_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["user", "kind", "created_at"]),
        ]

    def __str__(self):
        return f"Usage({self.user_id} {self.kind} {self.tokens_in}+{self.tokens_out})"


class UnansweredStatus(models.TextChoices):
    NEW = "new", "New"
    REVIEWED = "reviewed", "Reviewed"
    ANSWERED = "answered", "Answered"
    DISMISSED = "dismissed", "Dismissed"


class UnansweredQuestion(TimestampedModel):
    """A question the bot could not confidently answer — a knowledge gap.

    Captured (per tenant) when a chat turn comes back low-confidence AND an AI
    filter judges it a genuine question the business should be able to answer
    (greetings/chit-chat/off-topic are dropped; the kept reason is stored).

    De-duplicated per tenant on ``question_key`` (a normalised form of the
    question), so the same gap asked many ways bumps ``occurrences`` instead of
    creating rows. Review by frequency, then resolve into knowledge.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="unanswered_questions",
    )
    # The original question text (first occurrence) shown to reviewers.
    question = models.TextField()
    # Normalised dedup key (lowercased, punctuation stripped, whitespace collapsed).
    question_key = models.CharField(max_length=500, db_index=True)
    language = models.CharField(max_length=8, blank=True, default="")
    reason = models.TextField(blank=True, default="", help_text="Why the AI kept it.")
    status = models.CharField(
        max_length=12,
        choices=UnansweredStatus.choices,
        default=UnansweredStatus.NEW,
    )
    occurrences = models.PositiveIntegerField(default=1)
    last_asked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-last_asked_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "last_asked_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "question_key"], name="unique_user_question_key"
            )
        ]

    def __str__(self):
        return f"Unanswered({self.user_id} x{self.occurrences}: {self.question[:40]})"


class AvailableLanguage(TimestampedModel):
    """A language the guided question tree can be presented in.

    The canonical language (settings.GUIDED_TREE_CANONICAL_LANGUAGE) is where all
    edits happen; every other active language is a generated mirror of it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=10, unique=True, help_text="e.g. 'ar', 'en'")
    name = models.CharField(max_length=64, help_text="Display name, e.g. 'العربية'")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class QuestionTreeNode(TimestampedModel):
    """A node in a tenant's guided question tree.

    Tap a node → get its ``answer`` and/or its child questions to drill into.
    The tree exists once per language: the canonical language is authored, and
    every other language is a MIRROR matched **by position** (same ``order`` under
    the matching parent), never by foreign key. ``order`` must be unique among a
    node's active siblings (per owner + language) or positional matching breaks —
    enforced in the write path (services.guided_tree).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="question_tree_nodes",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    title = models.CharField(max_length=500)
    answer = models.TextField(null=True, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    language = models.CharField(max_length=10, default="ar", db_index=True)

    class Meta:
        ordering = ["order", "created_at"]
        indexes = [
            models.Index(fields=["owner", "language", "parent"]),
            models.Index(fields=["owner", "language", "is_active"]),
        ]

    def __str__(self):
        return f"[{self.language}] {self.title[:50]}"

    def is_root(self) -> bool:
        return self.parent_id is None

    def has_children(self) -> bool:
        return self.children.filter(is_active=True).exists()

    def get_children(self):
        return self.children.filter(is_active=True).order_by("order", "created_at")
