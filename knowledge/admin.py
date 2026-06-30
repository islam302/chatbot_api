from django.contrib import admin

from .models import (
    ChatbotConfig,
    ChatFeedback,
    DocumentChunk,
    TenantQuota,
    UnansweredQuestion,
    UploadedDocument,
    UsageRecord,
)


@admin.register(UnansweredQuestion)
class UnansweredQuestionAdmin(admin.ModelAdmin):
    list_display = ("question", "user", "status", "occurrences", "language", "last_asked_at")
    list_filter = ("status", "language")
    search_fields = ("question", "user__username")
    readonly_fields = ("id", "question_key", "created_at", "updated_at")


@admin.register(ChatbotConfig)
class ChatbotConfigAdmin(admin.ModelAdmin):
    list_display = ("user", "assistant_name", "company_name", "tone", "strict_grounding")
    list_filter = ("tone", "strict_grounding")
    search_fields = ("user__username", "assistant_name", "company_name")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(UploadedDocument)
class UploadedDocumentAdmin(admin.ModelAdmin):
    list_display = ("filename", "processing_status", "is_active", "uploaded_by", "created_at")
    list_filter = ("processing_status", "is_active", "created_at")
    search_fields = ("filename",)
    readonly_fields = ("id", "filename", "file_size", "error_message", "created_at", "updated_at")


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ("document", "position", "embedding_model", "created_at")
    list_filter = ("embedding_model",)
    search_fields = ("content",)
    readonly_fields = ("id", "embedding", "embedding_model", "created_at", "updated_at")


@admin.register(ChatFeedback)
class ChatFeedbackAdmin(admin.ModelAdmin):
    list_display = ("rating", "source", "question", "user", "created_at")
    list_filter = ("rating", "source")
    search_fields = ("question", "answer", "comment")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(TenantQuota)
class TenantQuotaAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "max_documents",
        "max_total_mb",
        "max_requests_per_min",
        "monthly_token_cap",
        "is_suspended",
    )
    list_filter = ("is_suspended",)
    search_fields = ("user__username",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "kind",
        "model",
        "tokens_in",
        "tokens_out",
        "cost_usd",
        "confident",
        "created_at",
    )
    list_filter = ("kind", "model", "confident", "created_at")
    search_fields = ("user__username",)
    readonly_fields = ("id", "created_at", "updated_at")
