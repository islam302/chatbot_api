"""Knowledge-gap capture: AI filter, dedupe, and the control API."""

from __future__ import annotations

from unittest import mock

from django.urls import reverse
from rest_framework.test import APITestCase

from knowledge.models import UnansweredQuestion, UnansweredStatus
from knowledge.services import gaps

from .factories import make_tenant


class RecordUnansweredTests(APITestCase):
    def setUp(self):
        self.user, _ = make_tenant("alice")

    def _keep(self):
        return mock.patch.object(gaps, "classify_question", return_value=(True, "in domain"))

    def _drop(self):
        return mock.patch.object(gaps, "classify_question", return_value=(False, "off topic"))

    def test_relevant_question_is_stored(self):
        with self._keep():
            obj = gaps.record_unanswered(self.user, "Do you support PostgreSQL backups?")
        self.assertIsNotNone(obj)
        self.assertEqual(obj.user, self.user)
        self.assertEqual(obj.status, UnansweredStatus.NEW)
        self.assertEqual(obj.occurrences, 1)

    def test_irrelevant_question_is_dropped(self):
        with self._drop():
            obj = gaps.record_unanswered(self.user, "What's the weather on Mars today?")
        self.assertIsNone(obj)
        self.assertEqual(UnansweredQuestion.objects.count(), 0)

    def test_trivial_is_skipped_without_classifying(self):
        with mock.patch.object(gaps, "classify_question") as clf:
            for q in ["hi", "/help", "شكرا", "ok"]:
                self.assertIsNone(gaps.record_unanswered(self.user, q))
        clf.assert_not_called()

    def test_duplicate_increments_occurrences(self):
        with self._keep() as clf:
            gaps.record_unanswered(self.user, "Do you ship to Iraq?")
            gaps.record_unanswered(self.user, "do you   ship to iraq?")  # same, normalized
        self.assertEqual(UnansweredQuestion.objects.filter(user=self.user).count(), 1)
        row = UnansweredQuestion.objects.get(user=self.user)
        self.assertEqual(row.occurrences, 2)
        clf.assert_called_once()  # classified once, not per occurrence


class UnansweredApiTests(APITestCase):
    def setUp(self):
        self.alice, self.alice_key = make_tenant("alice")
        self.bob, self.bob_key = make_tenant("bob")
        self.gap = UnansweredQuestion.objects.create(
            user=self.alice, question="Do you offer SLAs?", question_key="do you offer slas?"
        )

    def test_list_scoped_to_tenant(self):
        res = self.client.get(reverse("unanswered-question-list"), HTTP_X_API_KEY=self.alice_key)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)
        # Bob sees none.
        res_b = self.client.get(reverse("unanswered-question-list"), HTTP_X_API_KEY=self.bob_key)
        self.assertEqual(res_b.data["count"], 0)

    def test_filter_by_status(self):
        res = self.client.get(
            reverse("unanswered-question-list") + "?status=new", HTTP_X_API_KEY=self.alice_key
        )
        self.assertEqual(res.data["count"], 1)

    def test_update_status(self):
        url = reverse("unanswered-question-detail", args=[self.gap.id])
        res = self.client.patch(url, {"status": "dismissed"}, format="json", HTTP_X_API_KEY=self.alice_key)
        self.assertEqual(res.status_code, 200)
        self.gap.refresh_from_db()
        self.assertEqual(self.gap.status, "dismissed")

    def test_cannot_create_via_post(self):
        res = self.client.post(
            reverse("unanswered-question-list"), {"question": "x"}, format="json",
            HTTP_X_API_KEY=self.alice_key,
        )
        self.assertEqual(res.status_code, 405)

    def test_cannot_touch_other_tenant(self):
        url = reverse("unanswered-question-detail", args=[self.gap.id])
        res = self.client.patch(url, {"status": "dismissed"}, format="json", HTTP_X_API_KEY=self.bob_key)
        self.assertEqual(res.status_code, 404)
