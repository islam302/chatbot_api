import time
from typing import Optional

from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import QuestionAnswer
from ..serializers import (
    ChatFeedbackSerializer,
    ChatRequestSerializer,
    ChatResponseSerializer,
    QuestionSearchSerializer,
)
from ..services.rag import RagUnavailable, answer_question


def detect_language(text: str) -> str:
    """Detect language from text. Returns 'ar' or 'en' by default."""
    try:
        from langdetect import detect
        detected = detect(text)
        # Map common codes to our supported languages
        lang_map = {
            "ar": "ar",
            "en": "en",
            "es": "es",
            "fr": "fr",
            "de": "de",
            "pt": "pt",
            "ur": "ur",
        }
        return lang_map.get(detected, detected)
    except Exception:
        # Default to Arabic if detection fails
        return "ar"


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
        language = data.get("language") or detect_language(question)

        started = time.monotonic()
        try:
            result = answer_question(question, history=history, language=language)
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
    """Plain LIKE search across the Q&A bank."""

    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    @extend_schema(request=QuestionSearchSerializer)
    def post(self, request):
        serializer = QuestionSearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        query = data["query"]
        limit = data["limit"]

        qs = QuestionAnswer.objects.filter(
            Q(question__icontains=query) | Q(answer__icontains=query)
        )[:limit]
        results = [
            {
                "id": str(q.id),
                "question": q.question,
                "answer": q.answer,
                "count": q.count,
            }
            for q in qs
        ]
        return Response({"results": results, "total": len(results)})
