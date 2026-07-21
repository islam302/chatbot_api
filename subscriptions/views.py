from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from Authentication.authentication import APIKeyAuthentication
from knowledge.services import quota

from .models import Plan, Subscription
from .serializers import (
    AssignSubscriptionSerializer,
    PlanSerializer,
    SubscriptionSerializer,
)

AUTH = [APIKeyAuthentication, JWTAuthentication, SessionAuthentication]


class IsAdminOrReadOnly(permissions.BasePermission):
    """Anyone authenticated can read plans; only admins can change them."""

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return request.user and request.user.is_authenticated
        return request.user and request.user.is_staff


class PlanViewSet(viewsets.ModelViewSet):
    """Browse plans (any authenticated user); manage them (admin only)."""

    queryset = Plan.objects.all()
    serializer_class = PlanSerializer
    authentication_classes = AUTH
    permission_classes = [IsAdminOrReadOnly]

    def get_queryset(self):
        qs = Plan.objects.all()
        # Non-admins only see active, sellable plans.
        if not (self.request.user and self.request.user.is_staff):
            qs = qs.filter(is_active=True)
        return qs


class SubscriptionViewSet(viewsets.ModelViewSet):
    """Admin-only control plane for assigning/inspecting subscriptions."""

    queryset = Subscription.objects.select_related("user", "plan").all()
    serializer_class = SubscriptionSerializer
    authentication_classes = AUTH
    permission_classes = [permissions.IsAdminUser]

    @extend_schema(request=AssignSubscriptionSerializer, responses={200: SubscriptionSerializer})
    def create(self, request, *args, **kwargs):
        """Assign (or move) a user to a plan for a billing period."""
        serializer = AssignSubscriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sub = serializer.save()
        return Response(SubscriptionSerializer(sub).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "user": {"type": "string", "description": "User id"},
                    "amount": {"type": "integer", "description": "Credits to add"},
                },
                "required": ["user", "amount"],
            }
        },
        responses={200: dict},
        description="Admin: add credits to a user's wallet (top-up).",
    )
    @action(detail=False, methods=["post"], url_path="add-credits")
    def add_credits(self, request):
        from django.contrib.auth import get_user_model

        from .services import add_credits, credit_balance

        user_id = request.data.get("user")
        try:
            amount = int(request.data.get("amount"))
        except (TypeError, ValueError):
            return Response({"detail": "amount must be an integer."}, status=400)
        if amount <= 0:
            return Response({"detail": "amount must be positive."}, status=400)
        target = get_user_model().objects.filter(pk=user_id).first()
        if target is None:
            return Response({"detail": "Unknown user."}, status=404)
        add_credits(target, amount)
        return Response({"user": str(target.pk), "balance": credit_balance(target)})


class PaddleWebhookView(APIView):
    """Public endpoint Paddle calls after a payment. Verifies the signature, then
    grants credits / sets the subscription. Never trusts an unsigned request."""

    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []

    @extend_schema(request=None, responses={200: None, 403: None})
    def post(self, request):
        import json
        import logging

        from .paddle import process_event, verify_signature

        raw = request.body
        signature = request.META.get("HTTP_PADDLE_SIGNATURE", "")
        if not verify_signature(raw, signature):
            return Response({"detail": "Invalid signature."}, status=status.HTTP_403_FORBIDDEN)
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return Response({"detail": "Invalid JSON."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = process_event(payload)
        except Exception:
            logging.getLogger(__name__).exception("Paddle webhook processing failed")
            # 200 so Paddle doesn't hammer retries on a bug we've already logged.
            return Response({"status": "error-logged"})
        return Response({"status": result})


class MySubscriptionView(APIView):
    """The caller's own subscription + live usage for the current period."""

    authentication_classes = AUTH
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _remaining(used, limit):
        """Remaining allowance. None = unlimited (limit of 0)."""
        if not limit:
            return None
        return max(0, limit - used)

    @extend_schema(responses={200: dict})
    def get(self, request):
        user = request.user
        limits = quota.effective_limits(user)
        questions_used = quota.questions_used_this_period(user)
        tokens_used = quota.tokens_used_this_month(user)
        doc_count, used_mb = quota.document_usage(user)
        used_mb = round(used_mb, 2)

        # Documents/storage always have a positive cap (never "unlimited").
        docs_remaining = max(0, limits.max_documents - doc_count)
        storage_remaining = round(max(0.0, limits.max_total_mb - used_mb), 2)

        sub = None
        try:
            sub = user.subscription
        except Subscription.DoesNotExist:
            sub = None

        from .services import credit_balance, credits_per_question

        balance = credit_balance(user)
        per_q = credits_per_question()

        return Response(
            {
                "subscription": SubscriptionSerializer(sub).data if sub else None,
                "on_free_tier": sub is None,
                "credits": {
                    "balance": balance,
                    "credits_per_question": per_q,
                    "questions_left": balance // per_q,
                },
                "usage": {
                    "questions_used": questions_used,
                    "questions_limit": limits.monthly_questions,            # 0 = unlimited
                    "questions_remaining": self._remaining(questions_used, limits.monthly_questions),
                    "tokens_used": tokens_used,
                    "tokens_limit": limits.monthly_token_cap,               # 0 = unlimited
                    "tokens_remaining": self._remaining(tokens_used, limits.monthly_token_cap),
                    "documents_used": doc_count,
                    "documents_limit": limits.max_documents,
                    "documents_remaining": docs_remaining,
                    "storage_mb_used": used_mb,
                    "storage_mb_limit": limits.max_total_mb,
                    "storage_mb_remaining": storage_remaining,
                    "requests_per_min": limits.max_requests_per_min,
                },
            }
        )
