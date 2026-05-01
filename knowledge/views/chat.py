import time

from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.authentication import TokenAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView

from ..auth import APIKeyAuthentication
from ..serializers import (
    ChatFeedbackSerializer,
    ChatRequestSerializer,
    ChatResponseSerializer,
)
from ..services.rag import RagUnavailable, answer_question


def detect_language(text: str) -> str:
    """Detect language from text. Returns 'ar' or 'en' by default."""
    try:
        from langdetect import detect
        detected = detect(text)
        lang_map = {"ar": "ar", "en": "en", "es": "es", "fr": "fr", "de": "de", "pt": "pt", "ur": "ur"}
        return lang_map.get(detected, detected)
    except Exception:
        return "ar"


class ChatAPIView(APIView):
    """Answer a question using RAG pipeline."""

    authentication_classes = [APIKeyAuthentication, TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

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
            result = answer_question(
                question,
                history=history,
                language=language,
                user=request.user,
            )
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
    """Record feedback on a chat answer."""

    authentication_classes = [APIKeyAuthentication, TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=ChatFeedbackSerializer, responses={201: ChatFeedbackSerializer})
    def post(self, request):
        serializer = ChatFeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        feedback = serializer.save(user=request.user)
        return Response(
            ChatFeedbackSerializer(feedback).data, status=status.HTTP_201_CREATED
        )
