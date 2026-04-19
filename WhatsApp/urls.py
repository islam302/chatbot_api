from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    WhatsAppAnalyticsViewSet,
    WhatsAppMessageViewSet,
    WhatsAppSendView,
    WhatsAppSessionViewSet,
    WhatsAppUserViewSet,
    WhatsAppWebhookView,
)

router = DefaultRouter()
router.register("users", WhatsAppUserViewSet, basename="whatsapp-user")
router.register("sessions", WhatsAppSessionViewSet, basename="whatsapp-session")
router.register("messages", WhatsAppMessageViewSet, basename="whatsapp-message")
router.register("analytics", WhatsAppAnalyticsViewSet, basename="whatsapp-analytics")

urlpatterns = [
    path("webhook/", WhatsAppWebhookView.as_view(), name="whatsapp-webhook"),
    path("send/", WhatsAppSendView.as_view(), name="whatsapp-send"),
    path("", include(router.urls)),
]
