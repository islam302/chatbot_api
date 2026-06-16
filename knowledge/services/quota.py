"""Per-tenant quota enforcement and usage metering.

A *tenant* is a ``User``. Limits resolve from the tenant's ``TenantQuota`` row,
falling back to the global ``TENANT_*`` defaults in settings. This module is the
single place that:

* answers "is this tenant allowed to do X right now?" (documents, size, rate,
  monthly tokens, suspension), and
* records a metered ``UsageRecord`` after a chat answer and estimates its cost.

Views translate the raised exceptions into HTTP responses (413 / 429 / 403).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from ..models import TenantQuota, UploadedDocument, UsageKind, UsageRecord


class QuotaError(Exception):
    """Base class for quota violations (carries an HTTP status hint)."""

    status_code = 403

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TenantSuspended(QuotaError):
    status_code = 403


class DocumentQuotaExceeded(QuotaError):
    status_code = 413  # Payload/quota too large


class RateLimitExceeded(QuotaError):
    status_code = 429


class TokenBudgetExceeded(QuotaError):
    status_code = 429


# --- Limit resolution -------------------------------------------------------


@dataclass
class EffectiveLimits:
    max_documents: int
    max_total_mb: float
    max_requests_per_min: int
    monthly_token_cap: int  # 0 = unlimited
    is_suspended: bool


def get_quota(user) -> TenantQuota:
    quota, _ = TenantQuota.objects.get_or_create(user=user)
    return quota


def effective_limits(user) -> EffectiveLimits:
    quota = get_quota(user)
    return EffectiveLimits(
        max_documents=(
            quota.max_documents
            if quota.max_documents is not None
            else int(getattr(settings, "TENANT_MAX_DOCUMENTS", 100))
        ),
        max_total_mb=(
            quota.max_total_mb
            if quota.max_total_mb is not None
            else float(getattr(settings, "TENANT_MAX_TOTAL_MB", 200))
        ),
        max_requests_per_min=(
            quota.max_requests_per_min
            if quota.max_requests_per_min is not None
            else int(getattr(settings, "TENANT_MAX_REQUESTS_PER_MIN", 60))
        ),
        monthly_token_cap=(
            quota.monthly_token_cap
            if quota.monthly_token_cap is not None
            else int(getattr(settings, "TENANT_MONTHLY_TOKEN_CAP", 0))
        ),
        is_suspended=quota.is_suspended,
    )


# --- Current consumption ----------------------------------------------------


def _docs_qs(user):
    return UploadedDocument.objects.filter(uploaded_by=user)


def document_usage(user) -> tuple[int, float]:
    """Return ``(document_count, total_mb)`` currently stored for the tenant."""
    agg = _docs_qs(user).aggregate(total=Sum("file_size"))
    total_bytes = agg["total"] or 0
    return _docs_qs(user).count(), total_bytes / (1024 * 1024)


def _month_start(now=None):
    now = now or timezone.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def tokens_used_this_month(user) -> int:
    start = _month_start()
    agg = UsageRecord.objects.filter(user=user, created_at__gte=start).aggregate(
        ti=Sum("tokens_in"), to=Sum("tokens_out")
    )
    return (agg["ti"] or 0) + (agg["to"] or 0)


def requests_in_last_minute(user) -> int:
    cutoff = timezone.now() - timedelta(seconds=60)
    return UsageRecord.objects.filter(
        user=user, kind=UsageKind.CHAT, created_at__gte=cutoff
    ).count()


# --- Enforcement ------------------------------------------------------------


def check_not_suspended(user) -> None:
    if effective_limits(user).is_suspended:
        raise TenantSuspended("This account is suspended. Contact the administrator.")


def check_document_quota(user, incoming_bytes: int) -> None:
    """Raise if adding ``incoming_bytes`` would breach the tenant's doc limits."""
    check_not_suspended(user)
    limits = effective_limits(user)
    count, used_mb = document_usage(user)

    if count + 1 > limits.max_documents:
        raise DocumentQuotaExceeded(
            f"Document limit reached ({limits.max_documents}). "
            f"Delete some documents or request a higher limit."
        )

    incoming_mb = (incoming_bytes or 0) / (1024 * 1024)
    if used_mb + incoming_mb > limits.max_total_mb:
        raise DocumentQuotaExceeded(
            f"Storage limit reached ({limits.max_total_mb:g} MB). "
            f"Currently using {used_mb:.1f} MB."
        )


def check_chat_allowed(user) -> None:
    """Raise if the tenant is suspended, rate-limited, or over its token cap."""
    check_not_suspended(user)
    limits = effective_limits(user)

    if requests_in_last_minute(user) >= limits.max_requests_per_min:
        raise RateLimitExceeded(
            f"Rate limit exceeded ({limits.max_requests_per_min} requests/min). "
            f"Please slow down."
        )

    if limits.monthly_token_cap and tokens_used_this_month(user) >= limits.monthly_token_cap:
        raise TokenBudgetExceeded(
            "Monthly token budget exhausted. It resets at the start of next month."
        )


# --- Metering ---------------------------------------------------------------


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    pricing = getattr(settings, "LLM_PRICING", {}).get(model)
    if not pricing:
        return 0.0
    return (tokens_in / 1_000_000) * pricing.get("input", 0.0) + (
        tokens_out / 1_000_000
    ) * pricing.get("output", 0.0)


def record_usage(
    user,
    *,
    model: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    response_time_ms: int = 0,
    confident: bool = True,
    chunk_count: int = 0,
    kind: str = UsageKind.CHAT,
) -> UsageRecord:
    """Persist one metered event and return it. Never raises for the caller."""
    return UsageRecord.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        kind=kind,
        model=model or "",
        tokens_in=max(0, tokens_in or 0),
        tokens_out=max(0, tokens_out or 0),
        cost_usd=estimate_cost(model, tokens_in or 0, tokens_out or 0),
        response_time_ms=max(0, response_time_ms or 0),
        confident=confident,
        chunk_count=max(0, chunk_count or 0),
    )
