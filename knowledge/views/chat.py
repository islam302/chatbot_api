import time

from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from Authentication.authentication import APIKeyAuthentication
from ..serializers import (
    ChatFeedbackSerializer,
    ChatRequestSerializer,
    ChatResponseSerializer,
)
from ..services import quota
from ..services.rag import RagUnavailable, answer_question
from ..services.unanswered import dispatch_capture

# Chat is multi-tenant: authenticate with the tenant's API key (or a JWT) so the
# answer is scoped to that user's own knowledge and chatbot config.
CHAT_AUTH = [APIKeyAuthentication, JWTAuthentication]


def detect_language(text: str) -> str:
    """Best-effort language detection from the question itself.

    The LLM also matches the user's language via the system prompt, so this is
    mainly a hint (e.g. for Arabic dialect handling). Arabic script is detected
    with no dependency; otherwise langdetect refines it, falling back to English.
    """
    # Any Arabic-script character → Arabic (covers Arabic + common dialects).
    if any("؀" <= ch <= "ۿ" for ch in text):
        return "ar"
    try:
        from langdetect import detect
        detected = detect(text)
        lang_map = {"ar": "ar", "en": "en", "es": "es", "fr": "fr", "de": "de", "pt": "pt", "ur": "ur"}
        return lang_map.get(detected, detected)
    except Exception:
        return "en"


class ChatAPIView(APIView):
    """Answer a question using the RAG pipeline, scoped to the caller's data."""

    authentication_classes = CHAT_AUTH
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=ChatRequestSerializer, responses={200: ChatResponseSerializer})
    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        question = data["question"]
        history = data.get("history") or []
        language = data.get("language") or detect_language(question)

        # Per-tenant gate: suspension, rate limit, monthly question/token budget,
        # and a credit pre-check (friendly early 402).
        try:
            quota.check_chat_allowed(request.user)
        except quota.QuotaError as exc:
            return Response({"detail": exc.message}, status=exc.status_code)

        # Airtight enforcement: ATOMICALLY reserve this question's credits BEFORE
        # answering. Concurrent requests can't overspend — only as many as the
        # balance covers get through; the rest get 402. Refunded if the answer
        # fails, so a user is never charged for a reply they didn't receive.
        if not quota.reserve_question_credits(request.user):
            return Response(
                {"detail": "You're out of credits. Upgrade your plan to keep asking questions."},
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

        started = time.monotonic()
        try:
            result = answer_question(
                question,
                history=history,
                language=language,
                user=request.user,
            )
        except RagUnavailable as exc:
            # Log the real cause; return a generic message (never leak internals).
            import logging

            logging.getLogger("knowledge").error("RAG unavailable: %s", exc)
            quota.refund_question_credits(request.user)  # not the user's fault
            return Response(
                {"detail": "The assistant is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:
            quota.refund_question_credits(request.user)  # never charge for a crash
            raise
        elapsed = int((time.monotonic() - started) * 1000)

        # Meter the call for this tenant (tokens, cost, latency, confidence).
        quota.record_usage(
            request.user,
            model=result.model,
            tokens_in=result.prompt_tokens,
            tokens_out=result.completion_tokens,
            response_time_ms=elapsed,
            confident=result.confident,
            chunk_count=len(result.sources),
        )

        # Capture ONLY an in-domain question the bot couldn't answer from the data
        # ("gap") — the model classified it, so greetings/off-topic are excluded.
        # De-duplicated + off the request path; never blocks the reply.
        if result.answer_status == "gap":
            dispatch_capture(user=request.user, question=question, language=language)

        cost = quota.estimate_cost(
            result.model, result.prompt_tokens, result.completion_tokens
        )
        return Response(
            ChatResponseSerializer(
                {
                    "answer": result.answer,
                    "source": result.source,
                    "source_id": result.source_id,
                    "sources": result.sources,
                    "confident": result.confident,
                    "response_time_ms": elapsed,
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                    "cost_usd": round(cost, 6),
                }
            ).data
        )


class ChatFeedbackAPIView(APIView):
    """Record feedback on a chat answer."""

    authentication_classes = CHAT_AUTH
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=ChatFeedbackSerializer, responses={201: ChatFeedbackSerializer})
    def post(self, request):
        serializer = ChatFeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        feedback = serializer.save(user=request.user)
        return Response(
            ChatFeedbackSerializer(feedback).data, status=status.HTTP_201_CREATED
        )
