"""Subscription plans for the multi-tenant chatbot.

A ``Plan`` is a sellable bundle of limits (monthly questions + a fair-use token
cap + document/storage/rate limits + which LLM model to use). A ``Subscription``
assigns one plan to a user (tenant) for a billing period.

Limits are *enforced* in ``knowledge.services.quota`` — this app only defines the
bundles and who is on which one. Selling by **questions/month** keeps it simple
for customers; the **token cap** is a safety net against abuse (huge prompts).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Plan(TimestampedModel):
    """A sellable bundle of usage limits."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True, default="")

    # Selling price (informational — billing/collection is handled elsewhere).
    price_usd = models.DecimalField(max_digits=9, decimal_places=2, default=0)

    # Core metric the customer buys. 0 = unlimited.
    monthly_questions = models.PositiveIntegerField(default=0)
    # Fair-use safety net on cost. 0 = unlimited.
    monthly_token_cap = models.PositiveBigIntegerField(default=0)
    # Credits granted to the wallet when this plan is assigned/renewed. Each chat
    # question costs ``settings.CREDITS_PER_QUESTION`` credits.
    included_credits = models.PositiveIntegerField(default=0)

    # Resource limits.
    max_documents = models.PositiveIntegerField(default=100)
    max_total_mb = models.FloatField(default=200)
    max_requests_per_min = models.PositiveIntegerField(default=60)

    # Which LLM answers for tenants on this plan (per-plan model routing).
    llm_model = models.CharField(max_length=64, default="gpt-4o")

    # Feature gate: may tenants on this plan import knowledge from an external API
    # (the sync-api-content endpoint)? Off for the free tier.
    allow_api_sync = models.BooleanField(default=True)

    # Paddle price id (price_...) this plan maps to. A paid webhook is matched
    # back to a Plan by this id.
    paddle_price_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "price_usd"]

    def __str__(self):
        return self.name


class SubscriptionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    EXPIRED = "expired", "Expired"
    CANCELED = "canceled", "Canceled"


class Subscription(TimestampedModel):
    """A user's current plan assignment for a billing period."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan = models.ForeignKey(
        Plan, on_delete=models.PROTECT, related_name="subscriptions"
    )
    status = models.CharField(
        max_length=16, choices=SubscriptionStatus.choices, default=SubscriptionStatus.ACTIVE
    )
    current_period_start = models.DateTimeField(default=timezone.now)
    current_period_end = models.DateTimeField()
    auto_renew = models.BooleanField(default=True)

    # Paddle linkage (blank for admin/manual subscriptions).
    paddle_subscription_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    paddle_customer_id = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "current_period_end"])]

    def __str__(self):
        return f"{self.user} → {self.plan} ({self.status})"

    @property
    def is_current(self) -> bool:
        """True when the subscription is active and within its period."""
        return (
            self.status == SubscriptionStatus.ACTIVE
            and self.current_period_end > timezone.now()
        )


class CreditWallet(TimestampedModel):
    """A tenant's spendable credit balance. One chat question costs
    ``settings.CREDITS_PER_QUESTION`` credits; when the balance can't cover a
    question the chat is blocked (402) until they top up or upgrade.

    Free tier: the wallet starts with ``settings.FREE_TIER_CREDITS`` and is
    LAZILY topped back up to that amount at the start of each calendar month
    (see ``services.get_wallet``) — i.e. 50 questions per month. Paid plans add
    ``Plan.included_credits`` on each payment instead.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="credit_wallet",
    )
    balance = models.PositiveIntegerField(default=0)
    # When the monthly free-tier grant was last applied (for lazy renewal).
    last_free_grant_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.user} — {self.balance} credits"


class PaddleWebhookEvent(TimestampedModel):
    """Records processed Paddle event ids so a re-delivered webhook is a no-op."""

    event_id = models.CharField(max_length=64, unique=True)
    event_type = models.CharField(max_length=64, blank=True, default="")

    def __str__(self):
        return f"{self.event_type} {self.event_id}"


class UserFeatureOverride(TimestampedModel):
    """Per-user feature toggles set by an admin from the dashboard.

    ``overrides`` maps a feature key (see ``subscriptions.features.FEATURES``) to
    an explicit ``True``/``False``. A key that is ABSENT means "inherit the
    default" (plan / tier based). An admin can thus grant a feature a plan
    wouldn't normally include, or revoke one, for a specific tenant — without
    changing their plan. Staff/superusers always have every feature regardless.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feature_overrides",
    )
    overrides = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"Features({self.user}): {self.overrides}"
