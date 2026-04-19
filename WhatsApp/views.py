import json
import logging

from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    WhatsAppAnalytics,
    WhatsAppMessage,
    WhatsAppSession,
    WhatsAppUser,
)
from .serializers import (
    WhatsAppAnalyticsSerializer,
    WhatsAppMessageSerializer,
    WhatsAppSendResponseSerializer,
    WhatsAppSendSerializer,
    WhatsAppSessionSerializer,
    WhatsAppUserSerializer,
)
from .services.conversation import (
    handle_incoming_message,
    log_message,
)
from .services.meta_client import (
    MetaWhatsAppClient,
    WhatsAppClientError,
    parse_incoming_message,
    parse_status_updates,
)

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class WhatsAppWebhookView(APIView):
    """Receive webhook events from the Meta WhatsApp Cloud API."""

    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.client = MetaWhatsAppClient()

    @extend_schema(
        parameters=[
            OpenApiParameter("hub.mode", str),
            OpenApiParameter("hub.verify_token", str),
            OpenApiParameter("hub.challenge", str),
        ],
        responses={200: str, 403: str},
    )
    def get(self, request):
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge", "")
        if self.client.verify_webhook(mode, token):
            return HttpResponse(challenge, content_type="text/plain")
        return HttpResponse("Verification failed", status=403)

    def post(self, request):
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return HttpResponse("Invalid JSON", status=400)

        # Status updates and incoming messages share the same envelope.
        for status_update in parse_status_updates(payload):
            logger.info(
                "WhatsApp status update: %s -> %s",
                status_update.get("id"),
                status_update.get("status"),
            )

        message_data = parse_incoming_message(payload)
        if message_data is None or not message_data["message_text"]:
            return HttpResponse("OK")

        try:
            reply, user, session = handle_incoming_message(message_data)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error processing incoming WhatsApp message")
            return HttpResponse("Error", status=500)

        try:
            sent_id = self.client.send_text(message_data["from_number"], reply)
            log_message(
                user=user,
                session=session,
                message_type="outgoing",
                text=reply,
                whatsapp_message_id=sent_id,
            )
        except WhatsAppClientError as exc:
            logger.error("Could not deliver reply to WhatsApp: %s", exc)
        return HttpResponse("OK")


class WhatsAppSendView(APIView):
    """Send a free-form text message to a WhatsApp user."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=WhatsAppSendSerializer,
        responses={200: WhatsAppSendResponseSerializer},
    )
    def post(self, request):
        serializer = WhatsAppSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        client = MetaWhatsAppClient()
        try:
            message_id = client.send_text(
                serializer.validated_data["to_number"],
                serializer.validated_data["message"],
            )
        except WhatsAppClientError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"message_id": message_id, "status": "sent"})


class WhatsAppUserViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = WhatsAppUser.objects.all()
    serializer_class = WhatsAppUserSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["is_active", "language_preference"]
    search_fields = ["phone_number", "profile_name"]
    ordering_fields = ["created_at", "last_message_at", "message_count"]
    ordering = ["-last_message_at"]


class WhatsAppSessionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = WhatsAppSession.objects.all()
    serializer_class = WhatsAppSessionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["session_type", "is_active", "user"]
    ordering_fields = ["created_at", "updated_at"]
    ordering = ["-updated_at"]


class WhatsAppMessageViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = WhatsAppMessage.objects.all()
    serializer_class = WhatsAppMessageSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["message_type", "status", "user", "session"]
    search_fields = ["message_text"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]


class WhatsAppAnalyticsViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = WhatsAppAnalytics.objects.all()
    serializer_class = WhatsAppAnalyticsSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["date"]
    ordering_fields = ["date"]
    ordering = ["-date"]
