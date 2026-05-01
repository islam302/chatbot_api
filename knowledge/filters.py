from django_filters import rest_framework as filters

from .models import UploadedDocument


class UploadedDocumentFilter(filters.FilterSet):
    class Meta:
        model = UploadedDocument
        fields = ["processing_status", "is_active", "uploaded_by"]
