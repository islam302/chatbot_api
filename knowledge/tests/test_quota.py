"""Per-tenant quota + rate-limit enforcement."""

from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from knowledge.models import TenantQuota, UsageKind, UsageRecord
from knowledge.services import quota

from .factories import make_document, make_tenant


class DocumentQuotaTests(TestCase):
    def setUp(self):
        self.user, _ = make_tenant("alice")

    def test_within_limits_passes(self):
        TenantQuota.objects.update_or_create(
            user=self.user, defaults={"max_documents": 5, "max_total_mb": 100}
        )
        quota.check_document_quota(self.user, incoming_bytes=1024)  # no raise

    def test_document_count_limit(self):
        TenantQuota.objects.update_or_create(
            user=self.user, defaults={"max_documents": 1}
        )
        make_document(self.user, filename="one.txt", size=10)
        with self.assertRaises(quota.DocumentQuotaExceeded):
            quota.check_document_quota(self.user, incoming_bytes=10)

    def test_total_size_limit(self):
        TenantQuota.objects.update_or_create(
            user=self.user, defaults={"max_total_mb": 1}
        )
        # Already using ~0.6 MB; adding another ~0.6 MB exceeds 1 MB.
        make_document(self.user, filename="big.txt", size=600_000)
        with self.assertRaises(quota.DocumentQuotaExceeded):
            quota.check_document_quota(self.user, incoming_bytes=600_000)

    def test_suspended_tenant_blocked(self):
        TenantQuota.objects.update_or_create(
            user=self.user, defaults={"is_suspended": True}
        )
        with self.assertRaises(quota.TenantSuspended):
            quota.check_document_quota(self.user, incoming_bytes=1)


class RateLimitTests(TestCase):
    def setUp(self):
        self.user, _ = make_tenant("bob")

    def _record(self, n):
        for _ in range(n):
            UsageRecord.objects.create(user=self.user, kind=UsageKind.CHAT)

    def test_under_limit_passes(self):
        TenantQuota.objects.update_or_create(
            user=self.user, defaults={"max_requests_per_min": 5}
        )
        self._record(4)
        quota.check_chat_allowed(self.user)  # no raise

    def test_over_limit_raises(self):
        TenantQuota.objects.update_or_create(
            user=self.user, defaults={"max_requests_per_min": 5}
        )
        self._record(5)
        with self.assertRaises(quota.RateLimitExceeded):
            quota.check_chat_allowed(self.user)

    def test_monthly_token_cap(self):
        TenantQuota.objects.update_or_create(
            user=self.user, defaults={"monthly_token_cap": 100}
        )
        UsageRecord.objects.create(
            user=self.user, kind=UsageKind.CHAT, tokens_in=60, tokens_out=50
        )
        with self.assertRaises(quota.TokenBudgetExceeded):
            quota.check_chat_allowed(self.user)


class CostEstimateTests(TestCase):
    def test_known_model_cost(self):
        # gpt-4o: $2.5 / 1M in, $10 / 1M out.
        cost = quota.estimate_cost("gpt-4o", 1_000_000, 1_000_000)
        self.assertAlmostEqual(cost, 12.5, places=4)

    def test_unknown_model_is_free(self):
        self.assertEqual(quota.estimate_cost("mystery-model", 1000, 1000), 0.0)
