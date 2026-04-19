from rest_framework import serializers

from .models import (
    WhatsAppAnalytics,
    WhatsAppMessage,
    WhatsAppSession,
    WhatsAppUser,
)


class WhatsAppUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsAppUser
        fields = [
            "id",
            "phone_number",
            "profile_name",
            "language_preference",
            "is_active",
            "message_count",
            "last_message_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "message_count", "last_message_at", "created_at", "updated_at"]


class WhatsAppSessionSerializer(serializers.ModelSerializer):
    user_phone = serializers.CharField(source="user.phone_number", read_only=True)

    class Meta:
        model = WhatsAppSession
        fields = [
            "id",
            "user",
            "user_phone",
            "session_type",
            "context_data",
            "is_active",
            "expires_at",
            "message_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "message_count", "created_at", "updated_at"]


class WhatsAppMessageSerializer(serializers.ModelSerializer):
    user_phone = serializers.CharField(source="user.phone_number", read_only=True)

    class Meta:
        model = WhatsAppMessage
        fields = [
            "id",
            "user",
            "user_phone",
            "session",
            "message_type",
            "message_text",
            "whatsapp_message_id",
            "status",
            "api_endpoint_used",
            "response_time_ms",
            "error_message",
            "metadata",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class WhatsAppAnalyticsSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsAppAnalytics
        fields = "__all__"


class WhatsAppSendSerializer(serializers.Serializer):
    to_number = serializers.CharField(max_length=20)
    message = serializers.CharField(max_length=4096)


class WhatsAppSendResponseSerializer(serializers.Serializer):
    message_id = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
