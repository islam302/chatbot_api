import time

from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import (
    ChatFeedback,
    FixedQuestion,
    QuestionAnswer,
    UnansweredQuestion,
)
from ..serializers import (
    ChatFeedbackSerializer,
    ChatRequestSerializer,
    ChatResponseSerializer,
    QuestionSearchSerializer,
)
from ..services.rag import RagUnavailable, answer_question


class ChatAPIView(APIView):
    """Answer a question using the Q&A bank → RAG → fallback pipeline."""

    permission_classes = [permissions.AllowAny]

    @extend_schema(request=ChatRequestSerializer, responses={200: ChatResponseSerializer})
    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        question = data["question"]
        history = data.get("history") or []

        started = time.monotonic()
        try:
            result = answer_question(question, history=history)
        except RagUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        elapsed = int((time.monotonic() - started) * 1000)

        return Response(
            ChatResponseSerializer(
                {
                    "answer": result.answer,
                    "source": result.source,
                    "source_id": result.source_id,
                    "sources": result.sources,
                    "confident": result.confident,
                    "response_time_ms": elapsed,
                }
            ).data
        )


class ChatFeedbackAPIView(APIView):
    """Record 👍/👎 feedback on a chat answer."""

    permission_classes = [permissions.AllowAny]

    @extend_schema(request=ChatFeedbackSerializer, responses={201: ChatFeedbackSerializer})
    def post(self, request):
        serializer = ChatFeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        feedback = serializer.save(
            user=request.user if request.user.is_authenticated else None
        )
        return Response(
            ChatFeedbackSerializer(feedback).data, status=status.HTTP_201_CREATED
        )


class QuestionSearchAPIView(APIView):
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    @extend_schema(request=QuestionSearchSerializer)
    def post(self, request):
        serializer = QuestionSearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        query = data["query"]
        question_type = data["question_type"]
        limit = data["limit"]

        results: list[dict] = []

        if question_type in {"fixed", "all"}:
            for q in FixedQuestion.objects.filter(
                Q(question__icontains=query) | Q(answer__icontains=query)
            )[:limit]:
                results.append(
                    {
                        "id": str(q.id),
                        "question": q.question,
                        "answer": q.answer,
                        "type": "fixed",
                        "count": q.count,
                    }
                )

        if question_type in {"dynamic", "all"}:
            for q in QuestionAnswer.objects.filter(
                Q(question__icontains=query) | Q(answer__icontains=query)
            )[:limit]:
                results.append(
                    {
                        "id": str(q.id),
                        "question": q.question,
                        "answer": q.answer,
                        "type": "dynamic",
                        "count": q.count,
                    }
                )

        if question_type in {"unanswered", "all"}:
            for q in UnansweredQuestion.objects.filter(question__icontains=query)[:limit]:
                results.append(
                    {
                        "id": str(q.id),
                        "question": q.question,
                        "answer": None,
                        "type": "unanswered",
                        "status": q.status,
                    }
                )

        return Response({"results": results[:limit], "total": len(results)})
