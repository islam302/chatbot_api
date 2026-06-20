from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status, viewsets
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

        return Response(
            {
                "subscription": SubscriptionSerializer(sub).data if sub else None,
                "on_free_tier": sub is None,
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
