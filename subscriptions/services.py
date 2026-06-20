"""Subscription resolution helpers used by the quota/enforcement layer.

Kept dependency-light: ``knowledge.services.quota`` imports these lazily so the
two apps don't hard-couple. A user with NO subscription falls back to the global
default limits (so existing tenants keep working as a free tier).
"""

from __future__ import annotations

from django.utils import timezone

from .models import Subscription, SubscriptionStatus


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
