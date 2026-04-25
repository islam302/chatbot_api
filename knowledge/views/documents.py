from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from ..filters import UploadedDocumentFilter
from ..models import DocumentStatus, UploadedDocument
from ..serializers import UploadedDocumentSerializer, UploadedDocumentWriteSerializer
from ..services.rag import RagService


class UploadedDocumentViewSet(viewsets.ModelViewSet):
    queryset = UploadedDocument.objects.all()
    serializer_class = UploadedDocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = UploadedDocumentFilter
    search_fields = ["filename"]
    ordering_fields = ["created_at", "updated_at", "file_size"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return UploadedDocumentWriteSerializer
        return UploadedDocumentSerializer

    def perform_create(self, serializer):
        instance = serializer.save(
            uploaded_by=self.request.user,
            processing_status=DocumentStatus.COMPLETED,
        )
        RagService.instance().invalidate()
        return instance

    def perform_destroy(self, instance):
        instance.file.delete(save=False)
        super().perform_destroy(instance)
        RagService.instance().invalidate()
