import time

from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import FixedQuestion, QuestionAnswer, UnansweredQuestion
from ..serializers import (
    ChatRequestSerializer,
    ChatResponseSerializer,
    QuestionSearchSerializer,
)
from ..services.rag import RagService, RagUnavailable


class ChatAPIView(APIView):
    """Answer a question using the configured RAG pipeline."""

    permission_classes = [permissions.AllowAny]

    @extend_schema(request=ChatRequestSerializer, responses={200: ChatResponseSerializer})
    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        question = data["question"]
        history = data.get("history") or []
        chat_type = data.get("chat_type", "rag")

        started = time.monotonic()

        if chat_type == "questions":
            answer = self._answer_from_questions(question)
            response = {
                "answer": answer or "Sorry, I could not find an answer to that question.",
                "sources": [],
                "chat_type": "questions",
            }
            if not answer:
                UnansweredQuestion.objects.get_or_create(question=question)
        else:
            try:
                result = RagService.instance().answer(question, history=history)
            except RagUnavailable as exc:
                return Response(
                    {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
                )
            response = {
                "answer": result.answer,
                "sources": result.sources,
                "chat_type": "rag",
            }

        response["response_time_ms"] = int((time.monotonic() - started) * 1000)
        return Response(ChatResponseSerializer(response).data)

    @staticmethod
    def _answer_from_questions(question: str) -> str | None:
        match = (
            QuestionAnswer.objects.filter(is_active=True, question__iexact=question).first()
            or FixedQuestion.objects.filter(is_active=True, question__iexact=question).first()
        )
        if match is None:
            match = (
                QuestionAnswer.objects.filter(is_active=True, question__icontains=question)
                .order_by("-count")
                .first()
            )
        if match is None:
            return None
        if isinstance(match, QuestionAnswer):
            match.increment_count()
        return match.answer


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
