import logging

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from ..filters import UploadedDocumentFilter
from ..models import DocumentStatus, UploadedDocument
from ..serializers import UploadedDocumentSerializer, UploadedDocumentWriteSerializer
from ..services.chunking import ingest_document

logger = logging.getLogger(__name__)


class UploadedDocumentViewSet(viewsets.ModelViewSet):
    queryset = UploadedDocument.objects.all()
    serializer_class = UploadedDocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
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
            processing_status=DocumentStatus.PENDING,
        )
        try:
            ingest_document(instance)
        except Exception:
            # Status/error already persisted by ingest_document.
            logger.exception("Document ingestion failed for %s", instance.id)
        return instance

    @extend_schema(responses={202: UploadedDocumentSerializer})
    @action(detail=True, methods=["post"], url_path="reindex")
    def reindex(self, request, pk=None):
        """Force re-chunking and re-embedding of an existing document."""
        instance = self.get_object()
        try:
            result = ingest_document(instance)
        except Exception as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        instance.refresh_from_db()
        return Response(
            {
                **UploadedDocumentSerializer(instance).data,
                "chunks_created": result.chunks_created,
            },
            status=status.HTTP_202_ACCEPTED,
        )
