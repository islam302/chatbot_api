from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from .views import (
    AnalyticsAPIView,
    AvailableLanguageViewSet,
    ChatAPIView,
    CustomTokenObtainPairView,
    ExcelImportView,
    FixedQuestionViewSet,
    QuestionAnswerViewSet,
    QuestionSearchAPIView,
    SimpleQuestionTreeViewSet,
    UnansweredQuestionViewSet,
    UploadedDocumentViewSet,
    UserViewSet,
)

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")
router.register("fixed-questions", FixedQuestionViewSet, basename="fixed-question")
router.register("questions", QuestionAnswerViewSet, basename="question")
router.register("unanswered-questions", UnansweredQuestionViewSet, basename="unanswered-question")
router.register("question-tree", SimpleQuestionTreeViewSet, basename="question-tree")
router.register("languages", AvailableLanguageViewSet, basename="language")
router.register("documents", UploadedDocumentViewSet, basename="document")

urlpatterns = [
    path("auth/login/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("chat/", ChatAPIView.as_view(), name="chat"),
    path("search/", QuestionSearchAPIView.as_view(), name="search"),
    path("analytics/", AnalyticsAPIView.as_view(), name="analytics"),
    path("imports/excel/", ExcelImportView.as_view(), name="import-excel"),
    path("", include(router.urls)),
]
