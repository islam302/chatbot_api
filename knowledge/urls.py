from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from .views import (
    ChatAPIView,
    ChatFeedbackAPIView,
    CustomTokenObtainPairView,
    SyncAPIContentView,
    UploadedDocumentViewSet,
    UserViewSet,
)

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")
router.register("documents", UploadedDocumentViewSet, basename="document")

urlpatterns = [
    path("auth/login/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("chat/", ChatAPIView.as_view(), name="chat"),
    path("chat/feedback/", ChatFeedbackAPIView.as_view(), name="chat-feedback"),
    path("sync-api-content/", SyncAPIContentView.as_view(), name="sync-api-content"),
    path("", include(router.urls)),
]
