"""POST /sync-api-content/ — auth, paid-only gate, validation, fetch/process
paths, and upstream/processing error handling (network mocked)."""

from __future__ import annotations

from unittest import mock

import requests
from django.urls import reverse
from rest_framework.test import APITestCase

from knowledge.services.api_content_processor import APIContentProcessingError
from knowledge.views import api_content as view_mod

from .factories import make_tenant


class SyncApiContentTests(APITestCase):
    def setUp(self):
        self.url = reverse("sync-api-content")
        # Staff tenant is credit-exempt AND allowed to sync (bypasses the gate).
        self.admin, self.admin_key = make_tenant("api_admin", is_staff=True)

    def _post(self, body, key=None):
        return self.client.post(
            self.url, body, format="json",
            **({"HTTP_X_API_KEY": key} if key else {}),
        )

    def test_requires_auth(self):
        self.assertEqual(self._post({"api_url": "https://x.com"}).status_code, 401)

    def test_free_tier_blocked_with_402(self):
        _user, key = make_tenant("api_freebie")  # non-staff, no paid plan
        res = self._post({"api_url": "https://x.com/data"}, key=key)
        self.assertEqual(res.status_code, 402)

    def test_missing_api_url_returns_400(self):
        res = self._post({}, key=self.admin_key)
        self.assertEqual(res.status_code, 400)

    def test_unsafe_internal_url_rejected(self):
        res = self._post({"api_url": "http://127.0.0.1/admin"}, key=self.admin_key)
        self.assertEqual(res.status_code, 400)

    def test_no_items_under_key_returns_400(self):
        fake = mock.Mock()
        fake.json.return_value = {"results": []}
        fake.raise_for_status.return_value = None
        with mock.patch.object(view_mod, "validate_public_url", lambda u: u), \
             mock.patch.object(view_mod.requests, "get", return_value=fake):
            res = self._post({"api_url": "https://api.x.com/items"}, key=self.admin_key)
        self.assertEqual(res.status_code, 400)

    def test_happy_path_processes_items(self):
        fake = mock.Mock()
        fake.json.return_value = {"results": [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]}
        fake.raise_for_status.return_value = None
        stats = {"processed": 2, "chunks_created": 2, "items_new": 2, "errors": 0}
        with mock.patch.object(view_mod, "validate_public_url", lambda u: u), \
             mock.patch.object(view_mod.requests, "get", return_value=fake), \
             mock.patch.object(view_mod.APIContentRAGProcessor, "process_items", return_value=stats) as proc:
            res = self._post(
                {"api_url": "https://api.x.com/items", "items_key": "results"},
                key=self.admin_key,
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "success")
        self.assertEqual(res.data["processed"], 2)
        proc.assert_called_once()

    def test_upstream_failure_returns_503(self):
        with mock.patch.object(view_mod, "validate_public_url", lambda u: u), \
             mock.patch.object(view_mod.requests, "get", side_effect=requests.ConnectionError("down")):
            res = self._post({"api_url": "https://api.x.com/items"}, key=self.admin_key)
        self.assertEqual(res.status_code, 503)

    def test_processing_error_returns_500(self):
        fake = mock.Mock()
        fake.json.return_value = {"results": [{"id": 1}]}
        fake.raise_for_status.return_value = None
        with mock.patch.object(view_mod, "validate_public_url", lambda u: u), \
             mock.patch.object(view_mod.requests, "get", return_value=fake), \
             mock.patch.object(
                 view_mod.APIContentRAGProcessor, "process_items",
                 side_effect=APIContentProcessingError("boom")):
            res = self._post({"api_url": "https://api.x.com/items"}, key=self.admin_key)
        self.assertEqual(res.status_code, 500)

    def test_unexpected_error_returns_500(self):
        fake = mock.Mock()
        fake.json.return_value = {"results": [{"id": 1}]}
        fake.raise_for_status.return_value = None
        with mock.patch.object(view_mod, "validate_public_url", lambda u: u), \
             mock.patch.object(view_mod.requests, "get", return_value=fake), \
             mock.patch.object(view_mod.APIContentRAGProcessor, "process_items",
                               side_effect=RuntimeError("unexpected")):
            res = self._post({"api_url": "https://api.x.com/items"}, key=self.admin_key)
        self.assertEqual(res.status_code, 500)

    def test_gate_fails_open_when_billing_layer_errors(self):
        # If can_sync_api_content raises, the endpoint must not block (fail open).
        fake = mock.Mock()
        fake.json.return_value = {"results": [{"id": 1}]}
        fake.raise_for_status.return_value = None
        with mock.patch("subscriptions.services.can_sync_api_content", side_effect=RuntimeError), \
             mock.patch.object(view_mod, "validate_public_url", lambda u: u), \
             mock.patch.object(view_mod.requests, "get", return_value=fake), \
             mock.patch.object(view_mod.APIContentRAGProcessor, "process_items", return_value={"processed": 1}):
            # Use a normal (non-staff) user so only the fail-open path lets it through.
            _user, key = make_tenant("api_failopen")
            res = self._post({"api_url": "https://api.x.com/items"}, key=key)
        self.assertEqual(res.status_code, 200)
