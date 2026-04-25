from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..filters import (
    FixedQuestionFilter,
    QuestionAnswerFilter,
    UnansweredQuestionFilter,
)
from ..models import FixedQuestion, QuestionAnswer, UnansweredQuestion
from ..permissions import IsOwnerOrReadOnly
from ..serializers import (
    BulkQuestionUpdateSerializer,
    FixedQuestionSerializer,
    FixedQuestionWriteSerializer,
    QuestionAnswerSerializer,
    QuestionAnswerWriteSerializer,
    UnansweredQuestionSerializer,
    UnansweredQuestionWriteSerializer,
)


class FixedQuestionViewSet(viewsets.ModelViewSet):
    queryset = FixedQuestion.objects.all()
    serializer_class = FixedQuestionSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = FixedQuestionFilter
    search_fields = ["question", "answer", "overview_description"]
    ordering_fields = ["created_at", "updated_at", "count"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return FixedQuestionWriteSerializer
        return FixedQuestionSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @extend_schema(
        parameters=[OpenApiParameter("limit", int, description="Default 10")],
    )
    @action(detail=False, methods=["get"], url_path="most-asked")
    def most_asked(self, request):
        limit = int(request.query_params.get("limit", 10))
        qs = self.filter_queryset(self.get_queryset()).order_by("-count")[:limit]
        return Response(FixedQuestionSerializer(qs, many=True).data)


class QuestionAnswerViewSet(viewsets.ModelViewSet):
    queryset = QuestionAnswer.objects.all()
    serializer_class = QuestionAnswerSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = QuestionAnswerFilter
    search_fields = ["question", "answer", "overview_description"]
    ordering_fields = ["created_at", "updated_at", "count"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return QuestionAnswerWriteSerializer
        return QuestionAnswerSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_destroy(self, instance):
        if instance.is_fixed:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                "Fixed questions can only be removed via the fixed-questions endpoint."
            )
        instance.delete()

    @action(detail=True, methods=["post"], url_path="increment-count")
    def increment_count(self, request, pk=None):
        question = self.get_object()
        question.increment_count()
        return Response(QuestionAnswerSerializer(question).data)

    @extend_schema(request=BulkQuestionUpdateSerializer)
    @action(detail=False, methods=["post"], url_path="bulk-update")
    def bulk_update(self, request):
        serializer = BulkQuestionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data["question_ids"]
        action_type = serializer.validated_data["action"]

        qs = self.get_queryset().filter(id__in=ids)
        if action_type == "activate":
            updated = qs.update(is_active=True)
        elif action_type == "deactivate":
            updated = qs.update(is_active=False)
        else:  # delete
            updated, _ = qs.delete()
        return Response({"action": action_type, "affected": updated})


class UnansweredQuestionViewSet(viewsets.ModelViewSet):
    queryset = UnansweredQuestion.objects.all()
    serializer_class = UnansweredQuestionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = UnansweredQuestionFilter
    search_fields = ["question"]
    ordering_fields = ["created_at", "updated_at", "priority", "status"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return UnansweredQuestionWriteSerializer
        return UnansweredQuestionSerializer

    @extend_schema(
        request=None,
        parameters=[OpenApiParameter("user_id", int, required=True)],
        responses={200: UnansweredQuestionSerializer},
    )
    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        from django.contrib.auth import get_user_model

        question = self.get_object()
        user_id = request.query_params.get("user_id") or request.data.get("user_id")
        if not user_id:
            return Response(
                {"detail": "user_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        User = get_user_model()
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND
            )

        question.assigned_to = user
        question.status = "in_progress"
        question.save(update_fields=["assigned_to", "status", "updated_at"])
        return Response(UnansweredQuestionSerializer(question).data)
