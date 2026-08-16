"""Subscription resolution helpers used by the quota/enforcement layer.

Kept dependency-light: ``knowledge.services.quota`` imports these lazily so the
two apps don't hard-couple. A user with NO subscription falls back to the global
default limits (so existing tenants keep working as a free tier).
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from .models import CreditWallet, Plan, Subscription, SubscriptionStatus


def resolve_plan(ref):
    """Look up a Plan by slug or UUID. Returns the Plan or None."""
    if not ref:
        return None
    plan = Plan.objects.filter(slug=str(ref)).first()
    if plan is not None:
        return plan
    try:
        return Plan.objects.filter(pk=ref).first()
    except Exception:
        return None


def assign_plan(user, plan, *, duration_days=30, auto_renew=True):
    """Put ``user`` on ``plan`` (Plan instance or slug/UUID) for a period.

    Reusable for admin assignment and a future self-serve signup. Returns the
    Subscription, or None if the plan couldn't be resolved.
    """
    if not isinstance(plan, Plan):
        plan = resolve_plan(plan)
    if plan is None:
        return None
    now = timezone.now()
    sub, _ = Subscription.objects.update_or_create(
        user=user,
        defaults={
            "plan": plan,
            "status": SubscriptionStatus.ACTIVE,
            "current_period_start": now,
            "current_period_end": now + timedelta(days=int(duration_days or 30)),
            "auto_renew": auto_renew,
        },
    )
    # Grant the plan's credit allotment on assignment/renewal.
    if plan.included_credits:
        add_credits(user, plan.included_credits)
    return sub


# --- Credits ----------------------------------------------------------------


def get_wallet(user) -> CreditWallet:
    """The tenant's wallet, created on first use with the free-tier grant.

    Free-tier wallets RENEW monthly (lazily): on the first access in a new
    calendar month the balance is topped back up to ``FREE_TIER_CREDITS``
    (never reduced — admin top-ups above it are kept). Paid-plan tenants are
    skipped; their credits come from payments.
    """
    wallet, created = CreditWallet.objects.get_or_create(
        user=user,
        defaults={"balance": int(getattr(settings, "FREE_TIER_CREDITS", 0))},
    )
    if not created:
        _maybe_renew_free_credits(wallet, user)
    return wallet


def _maybe_renew_free_credits(wallet: CreditWallet, user) -> None:
    """Top a free-tier wallet back up to FREE_TIER_CREDITS once per calendar
    month. The conditional UPDATE is race-safe: concurrent first-of-month
    requests apply the grant exactly once."""
    if is_credit_exempt(user) or active_plan(user) is not None:
        return  # paid/staff tenants don't get the monthly free grant
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if wallet.last_free_grant_at >= month_start:
        return  # already granted this month
    from django.db.models.functions import Greatest
    from django.db.models import Value

    free = int(getattr(settings, "FREE_TIER_CREDITS", 0))
    updated = CreditWallet.objects.filter(
        pk=wallet.pk, last_free_grant_at__lt=month_start
    ).update(
        balance=Greatest(F("balance"), Value(free)),
        last_free_grant_at=now,
    )
    if updated:
        wallet.refresh_from_db(fields=["balance", "last_free_grant_at"])


def credit_balance(user) -> int:
    if user is None or not getattr(user, "is_authenticated", False):
        return 0
    return get_wallet(user).balance


def credits_per_question() -> int:
    return max(1, int(getattr(settings, "CREDITS_PER_QUESTION", 2)))


def is_credit_exempt(user) -> bool:
    """Staff/superusers never spend credits — their usage is internal testing.

    It is still metered in UsageRecord (tokens/cost analytics), just never
    charged and never blocked with 402.
    """
    return bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))


def has_credits_for_question(user) -> bool:
    if is_credit_exempt(user):
        return True
    return credit_balance(user) >= credits_per_question()


def add_credits(user, amount: int) -> int:
    """Add ``amount`` credits (top-up / plan grant). Returns the new balance."""
    amount = max(0, int(amount or 0))
    wallet = get_wallet(user)
    if amount:
        CreditWallet.objects.filter(pk=wallet.pk).update(balance=F("balance") + amount)
        wallet.refresh_from_db(fields=["balance"])
    return wallet.balance


def deduct_credits(user, amount: int | None = None) -> int:
    """Spend credits for one question (default ``CREDITS_PER_QUESTION``), never
    below zero. Returns the new balance. Not race-safe — for the chat gate use
    ``spend_credits`` instead."""
    cost = credits_per_question() if amount is None else max(0, int(amount))
    wallet = get_wallet(user)
    new_balance = max(0, wallet.balance - cost)
    if new_balance != wallet.balance:
        CreditWallet.objects.filter(pk=wallet.pk).update(balance=new_balance)
        wallet.balance = new_balance
    return wallet.balance


def spend_credits(user, amount: int | None = None) -> bool:
    """ATOMICALLY reserve credits for one question. Returns True only if the
    balance actually covered the cost and was decremented — this is the
    race-safe gate so concurrent requests can never overspend a wallet.

    The single conditional UPDATE (``balance >= cost`` → ``balance -= cost``) is
    atomic at the database, so two simultaneous requests on a wallet with only
    enough for one will see exactly one succeed and one fail.
    """
    if is_credit_exempt(user):
        return True  # admin/staff testing is free — nothing reserved
    cost = credits_per_question() if amount is None else max(0, int(amount))
    if cost == 0:
        return True
    wallet = get_wallet(user)  # ensure it exists (with the free grant)
    updated = CreditWallet.objects.filter(pk=wallet.pk, balance__gte=cost).update(
        balance=F("balance") - cost
    )
    return bool(updated)


def refund_credits(user, amount: int | None = None) -> int:
    """Return credits reserved by ``spend_credits`` when the answer failed."""
    if is_credit_exempt(user):
        return credit_balance(user)  # nothing was reserved — nothing to refund
    cost = credits_per_question() if amount is None else max(0, int(amount))
    return add_credits(user, cost)


def set_paddle_subscription(
    user, plan, *, paddle_subscription_id="", paddle_customer_id="", period_end=None
):
    """Create/refresh a Subscription from a Paddle event WITHOUT granting credits.

    Credits are granted separately by ``transaction.completed`` so each payment
    grants exactly once (see subscriptions/paddle.py).
    """
    now = timezone.now()
    sub, _ = Subscription.objects.update_or_create(
        user=user,
        defaults={
            "plan": plan,
            "status": SubscriptionStatus.ACTIVE,
            "current_period_start": now,
            "current_period_end": period_end or (now + timedelta(days=30)),
            "auto_renew": True,
            "paddle_subscription_id": paddle_subscription_id,
            "paddle_customer_id": paddle_customer_id,
        },
    )
    return sub


def cancel_paddle_subscription(paddle_subscription_id: str):
    """Mark the subscription linked to this Paddle id as canceled."""
    if not paddle_subscription_id:
        return None
    sub = Subscription.objects.filter(
        paddle_subscription_id=paddle_subscription_id
    ).first()
    if sub is not None:
        sub.status = SubscriptionStatus.CANCELED
        sub.save(update_fields=["status", "updated_at"])
    return sub


def get_subscription(user):
    """Return the user's Subscription row, or None."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    try:
        return user.subscription
    except Subscription.DoesNotExist:
        return None


def active_plan(user):
    """Return the user's plan IF their subscription is active & in-period, else None."""
    sub = get_subscription(user)
    if sub and sub.is_current:
        return sub.plan
    return None


def subscription_state(user) -> str:
    """One of: 'none' (no subscription), 'active', or 'inactive' (expired/canceled)."""
    sub = get_subscription(user)
    if sub is None:
        return "none"
    return "active" if sub.is_current else "inactive"


def period_start(user):
    """Start of the current usage window: the subscription period, else None
    (callers fall back to a calendar month)."""
    sub = get_subscription(user)
    if sub and sub.is_current:
        return sub.current_period_start
    return None


def resolve_model(user):
    """LLM model for this user's plan, or None to use the global default."""
    plan = active_plan(user)
    return plan.llm_model if plan and plan.llm_model else None


def can_sync_api_content(user) -> bool:
    """May this tenant import knowledge from an external API (sync-api-content)?

    Delegates to the feature system so a per-user admin override applies. Default
    (no override): staff always; paid plan per ``allow_api_sync``; free tier per
    ``settings.FREE_TIER_ALLOW_API_SYNC``.
    """
    from .features import has_feature

    return has_feature(user, "api_sync")


def plan_limits(user):
    """Return the active plan's limits as a dict, or None if no active plan.

    Keys mirror EffectiveLimits fields so the quota layer can merge them.
    """
    plan = active_plan(user)
    if plan is None:
        return None
    return {
        "max_documents": plan.max_documents,
        "max_total_mb": plan.max_total_mb,
        "max_requests_per_min": plan.max_requests_per_min,
        "monthly_token_cap": plan.monthly_token_cap,
        "monthly_questions": plan.monthly_questions,
    }
