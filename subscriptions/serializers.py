from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers

from .models import Plan, Subscription

User = get_user_model()


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "price_usd",
            "monthly_questions",
            "monthly_token_cap",
            "max_documents",
            "max_total_mb",
            "max_requests_per_min",
            "llm_model",
            "is_active",
            "sort_order",
        ]
        read_only_fields = ["id"]


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = PlanSerializer(read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    is_current = serializers.BooleanField(read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id",
            "user",
            "username",
            "plan",
            "status",
            "current_period_start",
            "current_period_end",
            "auto_renew",
            "is_current",
            "created_at",
        ]
        read_only_fields = fields


class AssignSubscriptionSerializer(serializers.Serializer):
    """Admin: put a user on a plan for a billing period."""

    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    plan = serializers.PrimaryKeyRelatedField(queryset=Plan.objects.all())
    duration_days = serializers.IntegerField(default=30, min_value=1)
    auto_renew = serializers.BooleanField(default=True)

    def save(self):
        data = self.validated_data
        now = timezone.now()
        sub, _ = Subscription.objects.update_or_create(
            user=data["user"],
            defaults={
                "plan": data["plan"],
                "status": "active",
                "current_period_start": now,
                "current_period_end": now + timedelta(days=data["duration_days"]),
                "auto_renew": data["auto_renew"],
            },
        )
        return sub
