from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ChatAPIView,
    ChatbotConfigView,
    ChatFeedbackAPIView,
    SyncAPIContentView,
    UnansweredQuestionViewSet,
    UploadedDocumentViewSet,
    UsageAnalyticsView,
)

router = DefaultRouter()
router.register("documents", UploadedDocumentViewSet, basename="document")
router.register("unanswered", UnansweredQuestionViewSet, basename="unanswered")

urlpatterns = [
    path("chat/", ChatAPIView.as_view(), name="chat"),
    path("chat/feedback/", ChatFeedbackAPIView.as_view(), name="chat-feedback"),
    path("chatbot-config/", ChatbotConfigView.as_view(), name="chatbot-config"),
    path("sync-api-content/", SyncAPIContentView.as_view(), name="sync-api-content"),
    path("analytics/usage/", UsageAnalyticsView.as_view(), name="analytics-usage"),
    path("", include(router.urls)),
]
