from django.contrib import admin

from .models import (
    AvailableLanguage,
    ChatFeedback,
    DocumentChunk,
    FixedQuestion,
    QuestionAnswer,
    SimpleQuestionTree,
    UnansweredQuestion,
    UploadedDocument,
)


@admin.register(FixedQuestion)
class FixedQuestionAdmin(admin.ModelAdmin):
    list_display = ("question", "answer_type", "count", "is_active", "created_at")
    list_filter = ("answer_type", "is_active", "created_at")
    search_fields = ("question", "answer", "overview_description")
    readonly_fields = ("id", "count", "created_at", "updated_at")


@admin.register(QuestionAnswer)
class QuestionAnswerAdmin(admin.ModelAdmin):
    list_display = ("question", "answer_type", "count", "is_fixed", "is_active", "created_at")
    list_filter = ("answer_type", "is_fixed", "is_active", "created_at")
    search_fields = ("question", "answer", "overview_description")
    readonly_fields = ("id", "count", "created_at", "updated_at")


@admin.register(UnansweredQuestion)
class UnansweredQuestionAdmin(admin.ModelAdmin):
    list_display = ("question", "status", "priority", "assigned_to", "created_at")
    list_filter = ("status", "priority")
    search_fields = ("question",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(SimpleQuestionTree)
class SimpleQuestionTreeAdmin(admin.ModelAdmin):
    list_display = ("title", "language", "parent", "order", "is_active", "created_at")
    list_filter = ("language", "is_active")
    search_fields = ("title", "answer")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(AvailableLanguage)
class AvailableLanguageAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")


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
