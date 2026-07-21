from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import serializers

from .models import Plan, Subscription

User = get_user_model()


class PlanSerializer(serializers.ModelSerializer):
    # Optional: auto-derived from `name` when omitted.
    slug = serializers.SlugField(required=False, allow_blank=True)

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
            "included_credits",
            "max_documents",
            "max_total_mb",
            "max_requests_per_min",
            "llm_model",
            "allow_api_sync",
            "paddle_price_id",
            "is_active",
            "sort_order",
        ]
        read_only_fields = ["id"]

    def _unique_slug(self, validated_data, instance=None) -> str:
        """Use the provided slug, else slugify the name; guarantee uniqueness."""
        base = (validated_data.get("slug") or "").strip()
        if not base:
            name = validated_data.get("name") or (instance.name if instance else "")
            base = slugify(name) or "plan"
        slug, i = base, 2
        qs = Plan.objects.all()
        if instance is not None:
            qs = qs.exclude(pk=instance.pk)
        while qs.filter(slug=slug).exists():
            slug, i = f"{base}-{i}", i + 1
        return slug

    def create(self, validated_data):
        validated_data["slug"] = self._unique_slug(validated_data)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "slug" in validated_data or "name" in validated_data:
            validated_data["slug"] = self._unique_slug(validated_data, instance)
        return super().update(instance, validated_data)


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
        from .services import assign_plan

        data = self.validated_data
        # assign_plan sets the period AND grants the plan's included_credits.
        return assign_plan(
            data["user"],
            data["plan"],
            duration_days=data["duration_days"],
            auto_renew=data["auto_renew"],
        )
