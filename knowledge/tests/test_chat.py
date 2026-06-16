"""Chat endpoint: auth, quota gating, metering, validation — mocked internals."""

from __future__ import annotations

from unittest import mock

from django.urls import reverse
from rest_framework.test import APITestCase

from knowledge.models import TenantQuota, UsageKind, UsageRecord
from knowledge.services.rag import AnswerResult
from knowledge.views import chat as chat_view

from .factories import make_tenant


def _fake_answer(**kw):
    base = dict(
        answer="hello from the team",
        source="rag",
        confident=True,
        model="gpt-4o",
        prompt_tokens=120,
        completion_tokens=30,
        sources=[{"filename": "a.txt", "score": 0.8}],
    )
    base.update(kw)
    return AnswerResult(**base)


class ChatEndpointTests(APITestCase):
    def setUp(self):
        self.user, self.key = make_tenant("alice")
        self.url = reverse("chat")

    def _post(self, body, key=None):
        return self.client.post(
            self.url, body, format="json",
            **({"HTTP_X_API_KEY": key} if key else {}),
        )

    def test_requires_auth(self):
        res = self._post({"question": "hi"})
        self.assertEqual(res.status_code, 401)

    def test_happy_path_returns_answer_and_usage(self):
        with mock.patch.object(chat_view, "answer_question", return_value=_fake_answer()):
            res = self._post({"question": "what do you sell?"}, key=self.key)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["answer"], "hello from the team")
        self.assertEqual(res.data["prompt_tokens"], 120)
        self.assertEqual(res.data["completion_tokens"], 30)
        self.assertGreater(res.data["cost_usd"], 0)
        self.assertTrue(res.data["confident"])

    def test_usage_is_recorded(self):
        with mock.patch.object(chat_view, "answer_question", return_value=_fake_answer()):
            self._post({"question": "hi"}, key=self.key)
        rec = UsageRecord.objects.get(user=self.user, kind=UsageKind.CHAT)
        self.assertEqual(rec.tokens_in, 120)
        self.assertEqual(rec.tokens_out, 30)
        self.assertEqual(rec.chunk_count, 1)

    def test_rate_limit_returns_429(self):
        TenantQuota.objects.update_or_create(
            user=self.user, defaults={"max_requests_per_min": 1}
        )
        UsageRecord.objects.create(user=self.user, kind=UsageKind.CHAT)
        with mock.patch.object(chat_view, "answer_question", return_value=_fake_answer()) as aq:
            res = self._post({"question": "hi"}, key=self.key)
        self.assertEqual(res.status_code, 429)
        aq.assert_not_called()  # gated before the LLM runs

    def test_suspended_returns_403(self):
        TenantQuota.objects.update_or_create(
            user=self.user, defaults={"is_suspended": True}
        )
        res = self._post({"question": "hi"}, key=self.key)
        self.assertEqual(res.status_code, 403)

    def test_blank_question_rejected(self):
        res = self._post({"question": ""}, key=self.key)
        self.assertEqual(res.status_code, 400)

    def test_history_too_long_rejected(self):
        history = [{"role": "user", "content": "x"} for _ in range(50)]
        res = self._post({"question": "hi", "history": history}, key=self.key)
        self.assertEqual(res.status_code, 400)

    def test_valid_history_accepted(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        with mock.patch.object(chat_view, "answer_question", return_value=_fake_answer()):
            res = self._post({"question": "again", "history": history}, key=self.key)
        self.assertEqual(res.status_code, 200)


class DetectLanguageTests(APITestCase):
    def test_arabic_script_is_deterministic(self):
        # Any Arabic-script text is detected with no dependency on langdetect.
        self.assertEqual(chat_view.detect_language("مرحبا كيف الحال"), "ar")
        self.assertEqual(chat_view.detect_language("بتبيعو اي هنا"), "ar")

    def test_latin_text_is_not_arabic(self):
        # langdetect can mislabel short Latin text, but it must never be "ar".
        self.assertNotEqual(
            chat_view.detect_language("hello how are you today my friend"), "ar"
        )

    def test_empty_falls_back_without_error(self):
        self.assertIsInstance(chat_view.detect_language(""), str)
