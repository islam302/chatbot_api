from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets
from rest_framework_simplejwt.authentication import JWTAuthentication

from Authentication.authentication import APIKeyAuthentication
from ..models import UnansweredQuestion
from ..serializers import UnansweredQuestionSerializer


class UnansweredQuestionViewSet(viewsets.ModelViewSet):
    """Knowledge gaps for the authenticated tenant.

    Questions the bot couldn't answer (AI-filtered to keep only in-domain,
    meaningful ones). Review them, mark status (reviewed/answered/dismissed),
    or delete. Rows are created by the system, not via POST.
    """

    serializer_class = UnansweredQuestionSerializer
    authentication_classes = [APIKeyAuthentication, JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "patch", "delete", "head", "options"]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "language"]
    search_fields = ["question"]
    ordering_fields = ["last_asked_at", "occurrences", "created_at"]
    ordering = ["-last_asked_at"]

    def get_queryset(self):
        return UnansweredQuestion.objects.filter(user=self.request.user)
