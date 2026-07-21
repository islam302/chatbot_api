"""Subscription plans: limit resolution, question quota, model routing, API."""

from __future__ import annotations

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from knowledge.models import UsageKind, UsageRecord
from knowledge.services import quota
from knowledge.tests.factories import make_tenant

from subscriptions import services
from subscriptions.models import Plan, Subscription


def make_plan(slug="test", **kw):
    defaults = dict(
        name=slug.title(), slug=slug, monthly_questions=0, monthly_token_cap=0,
        max_documents=100, max_total_mb=200, max_requests_per_min=60, llm_model="gpt-4o",
    )
    defaults.update(kw)
    return Plan.objects.create(**defaults)


def subscribe(user, plan, *, days=30, status="active"):
    now = timezone.now()
    return Subscription.objects.create(
        user=user, plan=plan, status=status,
        current_period_start=now, current_period_end=now + timedelta(days=days),
    )


def chat_records(user, n):
    for _ in range(n):
        UsageRecord.objects.create(user=user, kind=UsageKind.CHAT, tokens_in=10, tokens_out=5)


class LimitResolutionTests(APITestCase):
    def setUp(self):
        self.user, _ = make_tenant("alice")

    def test_no_subscription_is_free_tier(self):
        # A non-staff tenant with no plan is on the free tier: tiny doc limits.
        limits = quota.effective_limits(self.user)
        self.assertEqual(limits.max_documents, 1)       # FREE_TIER_MAX_DOCUMENTS
        self.assertEqual(limits.max_total_mb, 0.5)      # FREE_TIER_MAX_TOTAL_MB
        self.assertEqual(limits.monthly_questions, 0)   # unlimited (no plan cap)
        quota.check_chat_allowed(self.user)             # no raise (free credits)

    def test_staff_gets_generous_defaults(self):
        admin, _ = make_tenant("owner_defaults", is_staff=True)
        limits = quota.effective_limits(admin)
        self.assertEqual(limits.max_documents, 100)     # global default, not free tier

    def test_plan_limits_override_defaults(self):
        subscribe(self.user, make_plan("starter", max_documents=25, monthly_questions=1000))
        limits = quota.effective_limits(self.user)
        self.assertEqual(limits.max_documents, 25)
        self.assertEqual(limits.monthly_questions, 1000)

    def test_resolve_model_from_plan(self):
        subscribe(self.user, make_plan("mini", llm_model="gpt-4o-mini"))
        self.assertEqual(services.resolve_model(self.user), "gpt-4o-mini")

    def test_no_plan_model_is_none(self):
        self.assertIsNone(services.resolve_model(self.user))


class QuestionQuotaTests(APITestCase):
    def setUp(self):
        self.user, _ = make_tenant("bob")

    def test_under_quota_ok(self):
        subscribe(self.user, make_plan(monthly_questions=2))
        chat_records(self.user, 1)
        quota.check_chat_allowed(self.user)  # no raise

    def test_over_quota_raises(self):
        subscribe(self.user, make_plan(monthly_questions=2))
        chat_records(self.user, 2)
        with self.assertRaises(quota.QuestionQuotaExceeded):
            quota.check_chat_allowed(self.user)

    def test_token_cap_still_enforced(self):
        subscribe(self.user, make_plan(monthly_questions=0, monthly_token_cap=100))
        UsageRecord.objects.create(user=self.user, kind=UsageKind.CHAT, tokens_in=80, tokens_out=40)
        with self.assertRaises(quota.TokenBudgetExceeded):
            quota.check_chat_allowed(self.user)

    def test_expired_subscription_blocks(self):
        subscribe(self.user, make_plan(monthly_questions=1000), days=-1)  # ended yesterday
        with self.assertRaises(quota.SubscriptionInactive):
            quota.check_chat_allowed(self.user)


class SubscriptionApiTests(APITestCase):
    def setUp(self):
        self.user, self.key = make_tenant("alice")
        self.admin, self.admin_key = make_tenant("root", is_staff=True)
        self.plan = make_plan("starter", monthly_questions=1000, price_usd=39)

    def test_plans_list_for_authenticated(self):
        res = self.client.get(reverse("plan-list"), HTTP_X_API_KEY=self.key)
        self.assertEqual(res.status_code, 200)
        slugs = {p["slug"] for p in res.data["results"]}
        self.assertIn("starter", slugs)

    def test_plan_create_requires_admin(self):
        body = {"name": "Hacker", "slug": "hacker", "monthly_questions": 999999}
        res = self.client.post(reverse("plan-list"), body, format="json", HTTP_X_API_KEY=self.key)
        self.assertIn(res.status_code, (403, 401))

    def test_admin_creates_plan_without_slug(self):
        body = {"name": "Pro Plus", "monthly_questions": 20000, "price_usd": "299.00"}
        res = self.client.post(
            reverse("plan-list"), body, format="json", HTTP_X_API_KEY=self.admin_key
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["slug"], "pro-plus")  # auto-derived from name

    def test_admin_assigns_subscription(self):
        body = {"user": str(self.user.id), "plan": str(self.plan.id), "duration_days": 30}
        res = self.client.post(
            reverse("subscription-list"), body, format="json", HTTP_X_API_KEY=self.admin_key
        )
        self.assertEqual(res.status_code, 201)
        self.assertTrue(Subscription.objects.filter(user=self.user, plan=self.plan).exists())

    def test_register_with_plan_creates_subscription(self):
        from django.contrib.auth import get_user_model

        body = {
            "username": "newco", "email": "newco@x.com",
            "password": "S0meStr0ng!Pass", "password_confirm": "S0meStr0ng!Pass",
            "plan": "starter", "plan_duration_days": 30,
        }
        res = self.client.post(
            reverse("user-list"), body, format="json", HTTP_X_API_KEY=self.admin_key
        )
        self.assertEqual(res.status_code, 201)
        self.assertIn("subscription", res.data)
        self.assertEqual(res.data["subscription"]["plan"], "Starter")
        u = get_user_model().objects.get(username="newco")
        self.assertTrue(Subscription.objects.filter(user=u, plan=self.plan).exists())

    def test_register_with_invalid_plan_returns_400(self):
        body = {
            "username": "newco2", "email": "newco2@x.com",
            "password": "S0meStr0ng!Pass", "password_confirm": "S0meStr0ng!Pass",
            "plan": "does-not-exist",
        }
        res = self.client.post(
            reverse("user-list"), body, format="json", HTTP_X_API_KEY=self.admin_key
        )
        self.assertEqual(res.status_code, 400)

    def test_register_without_plan_still_works(self):
        from django.contrib.auth import get_user_model

        body = {
            "username": "noplan", "email": "noplan@x.com",
            "password": "S0meStr0ng!Pass", "password_confirm": "S0meStr0ng!Pass",
        }
        res = self.client.post(
            reverse("user-list"), body, format="json", HTTP_X_API_KEY=self.admin_key
        )
        self.assertEqual(res.status_code, 201)
        self.assertNotIn("subscription", res.data)

    def test_my_subscription_free_tier(self):
        res = self.client.get(reverse("my-subscription"), HTTP_X_API_KEY=self.key)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["on_free_tier"])
        self.assertIn("usage", res.data)

    def test_my_subscription_with_plan(self):
        subscribe(self.user, self.plan)
        res = self.client.get(reverse("my-subscription"), HTTP_X_API_KEY=self.key)
        self.assertFalse(res.data["on_free_tier"])
        usage = res.data["usage"]
        self.assertEqual(usage["questions_limit"], 1000)
        self.assertEqual(usage["questions_used"], 0)
        self.assertEqual(usage["questions_remaining"], 1000)
        # Documents/storage usage + remaining are reported too.
        self.assertIn("documents_remaining", usage)
        self.assertIn("storage_mb_remaining", usage)


class CreditTests(APITestCase):
    def setUp(self):
        self.user, self.key = make_tenant("wallet_user")

    def test_free_wallet_starts_at_free_tier_credits(self):
        # Default FREE_TIER_CREDITS = 100 → 50 questions at 2 credits each.
        self.assertEqual(services.credit_balance(self.user), 100)
        self.assertTrue(services.has_credits_for_question(self.user))

    def test_deduct_defaults_to_two_per_question(self):
        services.deduct_credits(self.user)
        self.assertEqual(services.credit_balance(self.user), 98)

    def test_deduct_floors_at_zero(self):
        services.deduct_credits(self.user, 1000)
        self.assertEqual(services.credit_balance(self.user), 0)
        self.assertFalse(services.has_credits_for_question(self.user))

    def test_check_chat_allowed_blocks_when_out_of_credits(self):
        services.deduct_credits(self.user, 100)  # empty the wallet
        with self.assertRaises(quota.CreditsExhausted) as ctx:
            quota.check_chat_allowed(self.user)
        self.assertEqual(ctx.exception.status_code, 402)

    def test_check_chat_allowed_passes_with_credits(self):
        quota.check_chat_allowed(self.user)  # 100 credits → fine, no raise

    def test_assign_plan_grants_included_credits(self):
        plan = make_plan(slug="pro", included_credits=2000)
        services.assign_plan(self.user, plan)
        self.assertEqual(services.credit_balance(self.user), 2100)  # 100 free + 2000

    def test_admin_add_credits_endpoint(self):
        admin, admin_key = make_tenant("credit_admin", is_staff=True)
        res = self.client.post(
            reverse("subscription-add-credits"),
            {"user": str(self.user.pk), "amount": 50},
            format="json",
            HTTP_X_API_KEY=admin_key,
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["balance"], 150)

    def test_my_subscription_reports_credits(self):
        res = self.client.get(reverse("my-subscription"), HTTP_X_API_KEY=self.key)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["credits"]["balance"], 100)
        self.assertEqual(res.data["credits"]["credits_per_question"], 2)
        self.assertEqual(res.data["credits"]["questions_left"], 50)


class FreeTierLimitTests(APITestCase):
    def setUp(self):
        self.user, self.key = make_tenant("free_user")            # non-staff, no plan
        self.admin, self.admin_key = make_tenant("owner", is_staff=True)

    def test_free_tier_doc_and_storage_limits(self):
        limits = quota.effective_limits(self.user)
        self.assertEqual(limits.max_documents, 1)      # FREE_TIER_MAX_DOCUMENTS
        self.assertEqual(limits.max_total_mb, 0.5)     # FREE_TIER_MAX_TOTAL_MB

    def test_staff_is_not_free_tier(self):
        limits = quota.effective_limits(self.admin)
        self.assertEqual(limits.max_documents, 100)    # generous global default
        self.assertFalse(quota.is_free_tier(self.admin))

    def test_paid_plan_overrides_free_tier(self):
        plan = make_plan(slug="biz", max_documents=50, max_total_mb=100, included_credits=0)
        subscribe(self.user, plan)
        limits = quota.effective_limits(self.user)
        self.assertEqual(limits.max_documents, 50)
        self.assertFalse(quota.is_free_tier(self.user))

    def test_free_tier_second_upload_blocked(self):
        # First tiny doc ok; a second one breaches the 1-document free limit.
        quota.check_document_quota(self.user, 100_000)  # ~0.1 MB, allowed
        from knowledge.tests.factories import make_document

        make_document(self.user, size=100_000)
        with self.assertRaises(quota.DocumentQuotaExceeded):
            quota.check_document_quota(self.user, 100_000)

    def test_free_tier_cannot_sync_api_content(self):
        self.assertFalse(services.can_sync_api_content(self.user))
        res = self.client.post(
            reverse("sync-api-content"),
            {"api_url": "https://example.com/api/x"},
            format="json",
            HTTP_X_API_KEY=self.key,
        )
        self.assertEqual(res.status_code, 402)

    def test_admin_can_sync_api_content(self):
        self.assertTrue(services.can_sync_api_content(self.admin))

    def test_paid_plan_can_sync_when_enabled(self):
        plan = make_plan(slug="apiplan", allow_api_sync=True, included_credits=0)
        subscribe(self.user, plan)
        self.assertTrue(services.can_sync_api_content(self.user))


from django.test import override_settings

_WH_SECRET = "pdl_ntfset_testsecret"


def _sign(raw: bytes, secret=_WH_SECRET, ts="1700000000"):
    import hashlib
    import hmac

    h1 = hmac.new(secret.encode(), ts.encode() + b":" + raw, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={h1}"


@override_settings(PADDLE_WEBHOOK_SECRET=_WH_SECRET)
class PaddleWebhookTests(APITestCase):
    def setUp(self):
        self.user, _ = make_tenant("payer")
        self.plan = make_plan(slug="pro", included_credits=2000, paddle_price_id="pri_123")
        self.url = reverse("paddle-webhook")

    def _post(self, payload, *, secret=_WH_SECRET, sign=True):
        import json

        raw = json.dumps(payload).encode()
        headers = {}
        if sign:
            headers["HTTP_PADDLE_SIGNATURE"] = _sign(raw, secret)
        return self.client.post(
            self.url, data=raw, content_type="application/json", **headers
        )

    def _txn(self, event_id="evt_1"):
        return {
            "event_id": event_id,
            "event_type": "transaction.completed",
            "data": {
                "id": "txn_1",
                "custom_data": {"user_id": str(self.user.pk)},
                "items": [{"price": {"id": "pri_123"}}],
            },
        }

    def test_bad_signature_rejected(self):
        res = self._post(self._txn(), secret="wrong-secret")
        self.assertEqual(res.status_code, 403)

    def test_missing_signature_rejected(self):
        res = self._post(self._txn(), sign=False)
        self.assertEqual(res.status_code, 403)

    def test_transaction_completed_grants_credits(self):
        before = services.credit_balance(self.user)
        res = self._post(self._txn())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(services.credit_balance(self.user), before + 2000)

    def test_idempotent_on_repeated_event_id(self):
        before = services.credit_balance(self.user)
        self._post(self._txn(event_id="evt_dup"))
        self._post(self._txn(event_id="evt_dup"))  # same id → no second grant
        self.assertEqual(services.credit_balance(self.user), before + 2000)

    def test_subscription_created_sets_plan_without_granting(self):
        before = services.credit_balance(self.user)
        payload = {
            "event_id": "evt_sub",
            "event_type": "subscription.created",
            "data": {
                "id": "sub_1",
                "customer_id": "ctm_1",
                "custom_data": {"user_id": str(self.user.pk)},
                "items": [{"price": {"id": "pri_123"}}],
                "next_billed_at": "2026-12-31T00:00:00Z",
            },
        }
        res = self._post(payload)
        self.assertEqual(res.status_code, 200)
        sub = Subscription.objects.get(user=self.user)
        self.assertEqual(sub.plan, self.plan)
        self.assertEqual(sub.paddle_subscription_id, "sub_1")
        # subscription.* does NOT grant credits (transactions do).
        self.assertEqual(services.credit_balance(self.user), before)

    def test_subscription_canceled_marks_status(self):
        from subscriptions.models import SubscriptionStatus

        subscribe(self.user, self.plan)
        Subscription.objects.filter(user=self.user).update(paddle_subscription_id="sub_9")
        payload = {
            "event_id": "evt_cancel",
            "event_type": "subscription.canceled",
            "data": {"id": "sub_9"},
        }
        self._post(payload)
        sub = Subscription.objects.get(user=self.user)
        self.assertEqual(sub.status, SubscriptionStatus.CANCELED)

    def test_unknown_price_is_ignored_gracefully(self):
        payload = self._txn(event_id="evt_x")
        payload["data"]["items"] = [{"price": {"id": "pri_unknown"}}]
        res = self._post(payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "unmatched")


class AtomicSpendTests(APITestCase):
    def setUp(self):
        self.user, _ = make_tenant("spender")
        services.deduct_credits(self.user, 10_000)  # empty the free grant

    def test_spend_is_bounded_and_atomic(self):
        services.add_credits(self.user, 2)  # exactly one question's worth
        self.assertTrue(services.spend_credits(self.user))    # 2 -> 0
        self.assertFalse(services.spend_credits(self.user))   # nothing left
        self.assertEqual(services.credit_balance(self.user), 0)

    def test_spend_insufficient_leaves_balance_untouched(self):
        services.add_credits(self.user, 1)  # less than the 2-credit cost
        self.assertFalse(services.spend_credits(self.user))
        self.assertEqual(services.credit_balance(self.user), 1)

    def test_refund_returns_credits(self):
        services.add_credits(self.user, 2)
        services.spend_credits(self.user)
        services.refund_credits(self.user)
        self.assertEqual(services.credit_balance(self.user), 2)
