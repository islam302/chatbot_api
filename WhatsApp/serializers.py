from rest_framework import serializers

from .models import (
    WhatsAppAccount,
    WhatsAppAnalytics,
    WhatsAppMessage,
    WhatsAppSession,
    WhatsAppUser,
)


class WhatsAppAccountSerializer(serializers.ModelSerializer):
    tenant_username = serializers.CharField(source="tenant.username", read_only=True)

    class Meta:
        model = WhatsAppAccount
        fields = [
            "id",
            "tenant",
            "tenant_username",
            "phone_number_id",
            "access_token",
            "display_name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant_username", "created_at", "updated_at"]
        # Never return the send token in responses; accept it on write only.
        extra_kwargs = {"access_token": {"write_only": True, "required": False}}


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
