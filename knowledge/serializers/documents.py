from rest_framework import serializers

from ..models import UploadedDocument


class UploadedDocumentSerializer(serializers.ModelSerializer):
    file_size_mb = serializers.FloatField(read_only=True)
    chunk_count = serializers.IntegerField(source="chunks.count", read_only=True)
    uploaded_by_username = serializers.CharField(
        source="uploaded_by.username", read_only=True
    )

    class Meta:
        model = UploadedDocument
        fields = [
            "id",
            "file",
            "filename",
            "file_size",
            "file_size_mb",
            "chunk_count",
            "processing_status",
            "error_message",
            "is_active",
            "source_type",
            "api_url",
            "items_key",
            "uploaded_by",
            "uploaded_by_username",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "filename",
            "file_size",
            "processing_status",
            "error_message",
            "source_type",
            "api_url",
            "items_key",
            "uploaded_by",
            "created_at",
            "updated_at",
        ]


class UploadedDocumentWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = UploadedDocument
        fields = ["file", "is_active"]

    def create(self, validated_data):
        file = validated_data["file"]
        validated_data["filename"] = file.name
        validated_data["file_size"] = file.size
        return super().create(validated_data)
