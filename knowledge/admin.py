from django.contrib import admin

from .models import ChatFeedback, DocumentChunk, UploadedDocument


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
