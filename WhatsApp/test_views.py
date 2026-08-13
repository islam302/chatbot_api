"""WhatsApp HTTP surface: webhook verify/receive, send endpoint, and the
read-only viewsets' auth. Meta network calls are mocked."""

from __future__ import annotations

import json
from unittest import mock

from django.urls import reverse
from rest_framework.test import APITestCase

from knowledge.services.rag import AnswerResult
from knowledge.tests.factories import make_tenant

from WhatsApp import views as wv
from WhatsApp.services.meta_client import WhatsAppClientError


class WebhookVerifyTests(APITestCase):
    def setUp(self):
        self.url = reverse("whatsapp-webhook")

    def test_get_returns_challenge_when_token_valid(self):
        with mock.patch.object(wv, "MetaWhatsAppClient") as Client:
            Client.return_value.verify_webhook.return_value = True
            res = self.client.get(self.url, {"hub.mode": "subscribe",
                                             "hub.verify_token": "t", "hub.challenge": "42"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content.decode(), "42")

    def test_get_rejects_bad_token_with_403(self):
        with mock.patch.object(wv, "MetaWhatsAppClient") as Client:
            Client.return_value.verify_webhook.return_value = False
            res = self.client.get(self.url, {"hub.mode": "subscribe", "hub.verify_token": "x"})
        self.assertEqual(res.status_code, 403)


class WebhookReceiveTests(APITestCase):
    def setUp(self):
        self.url = reverse("whatsapp-webhook")

    def _post(self, payload):
        return self.client.post(self.url, data=json.dumps(payload), content_type="application/json")

    def test_invalid_json_returns_400(self):
        with mock.patch.object(wv, "MetaWhatsAppClient"):
            res = self.client.post(self.url, data="{not json", content_type="application/json")
        self.assertEqual(res.status_code, 400)

    def test_no_message_returns_ok(self):
        with mock.patch.object(wv, "MetaWhatsAppClient"), \
             mock.patch.object(wv, "parse_status_updates", return_value=[]), \
             mock.patch.object(wv, "parse_incoming_message", return_value=None):
            res = self._post({"entry": []})
        self.assertEqual(res.status_code, 200)

    def test_incoming_message_is_answered_and_reply_sent(self):
        tenant, _ = make_tenant("wa_acme")
        msg = {"message_text": "hi", "from_number": "20100", "phone_number_id": "PNID", "profile_name": "T"}
        with mock.patch.object(wv, "MetaWhatsAppClient") as Client, \
             mock.patch.object(wv, "parse_status_updates", return_value=[]), \
             mock.patch.object(wv, "parse_incoming_message", return_value=msg), \
             mock.patch.object(wv, "handle_incoming_message",
                               return_value=("reply text", None, None, None)), \
             mock.patch.object(wv, "log_message"):
            Client.return_value.send_text.return_value = "wamid.1"
            res = self._post({"entry": [{}]})
        self.assertEqual(res.status_code, 200)
        Client.return_value.send_text.assert_called_once()

    def test_status_updates_and_per_account_reply(self):
        # A status-update entry is logged, and when the handler returns a linked
        # account the reply is sent from THAT account's number (per-tenant).
        from WhatsApp.models import WhatsAppAccount

        tenant, _ = make_tenant("wa_peracct")
        account = WhatsAppAccount.objects.create(tenant=tenant, phone_number_id="PNID77")
        msg = {"message_text": "hi", "from_number": "20100", "phone_number_id": "PNID77", "profile_name": "T"}
        with mock.patch.object(wv, "MetaWhatsAppClient") as Client, \
             mock.patch.object(wv, "parse_status_updates",
                               return_value=[{"id": "m1", "status": "delivered"}]), \
             mock.patch.object(wv, "parse_incoming_message", return_value=msg), \
             mock.patch.object(wv, "handle_incoming_message",
                               return_value=("reply", None, None, account)), \
             mock.patch.object(wv, "log_message"):
            Client.return_value.send_text.return_value = "wamid.2"
            res = self._post({"entry": [{}]})
        self.assertEqual(res.status_code, 200)
        # Client was constructed twice: default in __init__, then per-account.
        self.assertGreaterEqual(Client.call_count, 2)

    def test_handler_error_returns_500(self):
        msg = {"message_text": "hi", "from_number": "20100", "phone_number_id": "P", "profile_name": "T"}
        with mock.patch.object(wv, "MetaWhatsAppClient"), \
             mock.patch.object(wv, "parse_status_updates", return_value=[]), \
             mock.patch.object(wv, "parse_incoming_message", return_value=msg), \
             mock.patch.object(wv, "handle_incoming_message", side_effect=RuntimeError("boom")):
            res = self._post({"entry": [{}]})
        self.assertEqual(res.status_code, 500)

    def test_delivery_failure_is_swallowed_returns_ok(self):
        msg = {"message_text": "hi", "from_number": "20100", "phone_number_id": "P", "profile_name": "T"}
        with mock.patch.object(wv, "MetaWhatsAppClient") as Client, \
             mock.patch.object(wv, "parse_status_updates", return_value=[]), \
             mock.patch.object(wv, "parse_incoming_message", return_value=msg), \
             mock.patch.object(wv, "handle_incoming_message",
                               return_value=("reply", None, None, None)), \
             mock.patch.object(wv, "log_message"):
            Client.return_value.send_text.side_effect = WhatsAppClientError("no route")
            res = self._post({"entry": [{}]})
        self.assertEqual(res.status_code, 200)


class SendEndpointTests(APITestCase):
    def setUp(self):
        self.url = reverse("whatsapp-send")
        self.user, _ = make_tenant("wa_sender")

    def test_requires_auth(self):
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 401)

    def test_invalid_body_returns_400(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post(self.url, {"message": "hi"}, format="json").status_code, 400)

    def test_sends_message(self):
        self.client.force_authenticate(self.user)
        with mock.patch.object(wv, "MetaWhatsAppClient") as Client:
            Client.return_value.send_text.return_value = "wamid.9"
            res = self.client.post(self.url, {"to_number": "20100", "message": "hi"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "sent")
        self.assertEqual(res.data["message_id"], "wamid.9")

    def test_client_error_returns_502(self):
        self.client.force_authenticate(self.user)
        with mock.patch.object(wv, "MetaWhatsAppClient") as Client:
            Client.return_value.send_text.side_effect = WhatsAppClientError("bad token")
            res = self.client.post(self.url, {"to_number": "20100", "message": "hi"}, format="json")
        self.assertEqual(res.status_code, 502)


class ReadOnlyViewsetAuthTests(APITestCase):
    def setUp(self):
        self.user, _ = make_tenant("wa_reader")

    def test_list_endpoints_require_auth(self):
        for name in ("whatsapp-user-list", "whatsapp-session-list",
                     "whatsapp-message-list", "whatsapp-analytics-list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 401, name)

    def test_list_endpoints_ok_when_authenticated(self):
        self.client.force_authenticate(self.user)
        for name in ("whatsapp-user-list", "whatsapp-session-list",
                     "whatsapp-message-list", "whatsapp-analytics-list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
