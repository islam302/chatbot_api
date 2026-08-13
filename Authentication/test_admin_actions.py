"""Admin-only user/API-key management actions and their error branches, plus
public register/verify edge cases — closing the endpoint coverage gaps."""

from __future__ import annotations

from unittest import mock

from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APITestCase

from Authentication.models import APIKey, User
from Authentication.tokens import email_verification_token

_SEND = "Authentication.views.send_activation_email"


class AdminUserActionTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", password="x", email="admin@x.com", is_staff=True, is_superuser=True
        )
        self.client.force_authenticate(self.admin)

    def _body(self, **over):
        b = {
            "username": "newbie",
            "email": "newbie@example.com",
            "password": "Str0ng-Passw0rd!",
            "password_confirm": "Str0ng-Passw0rd!",
        }
        b.update(over)
        return b

    def test_create_user_alias_creates_account(self):
        res = self.client.post(reverse("user-create-user"), self._body(), format="json")
        self.assertEqual(res.status_code, 201)
        self.assertTrue(User.objects.filter(username="newbie").exists())

    def test_register_alias_creates_account(self):
        res = self.client.post(
            reverse("user-register"), self._body(username="reg", email="reg@x.com"), format="json"
        )
        self.assertEqual(res.status_code, 201)

    def test_create_with_unknown_plan_rejected(self):
        res = self.client.post(reverse("user-list"), self._body(plan="no-such-plan"), format="json")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_create_reports_send_failed_when_email_errors(self):
        with mock.patch(_SEND, side_effect=RuntimeError("smtp down")):
            res = self.client.post(reverse("user-list"), self._body(), format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["email_verification"], "send_failed")

    def test_non_admin_cannot_create(self):
        plain = User.objects.create_user(username="plain", password="x")
        self.client.force_authenticate(plain)
        res = self.client.post(reverse("user-list"), self._body(), format="json")
        self.assertEqual(res.status_code, 403)


class ResendActivationTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", password="x", is_staff=True, is_superuser=True
        )
        self.client.force_authenticate(self.admin)

    def test_already_verified_is_noop_ok(self):
        u = User.objects.create_user(username="done", email="d@x.com", password="x")
        u.is_active = True
        u.email_verified = True
        u.save()
        res = self.client.post(reverse("user-resend-activation", args=[u.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertIn("already verified", res.data["detail"])

    def test_no_email_returns_400(self):
        u = User.objects.create_user(username="noemail", password="x")
        u.is_active = False
        u.save()
        res = self.client.post(reverse("user-resend-activation", args=[u.pk]))
        self.assertEqual(res.status_code, 400)

    def test_send_failure_returns_503(self):
        u = User.objects.create_user(username="failmail", email="f@x.com", password="x")
        u.is_active = False
        u.save()
        with mock.patch(_SEND, side_effect=RuntimeError("down")):
            res = self.client.post(reverse("user-resend-activation", args=[u.pk]))
        self.assertEqual(res.status_code, 503)


class AdminPasswordAndKeyTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", password="x", is_staff=True, is_superuser=True
        )
        self.target = User.objects.create_user(username="target", email="t@x.com", password="OldPass!123")
        APIKey.objects.get_or_create(user=self.target)
        self.client.force_authenticate(self.admin)

    def test_admin_set_password(self):
        res = self.client.post(
            reverse("user-set-password", args=[self.target.pk]),
            {"new_password": "Br4nd-New-Pass!"}, format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password("Br4nd-New-Pass!"))

    def test_non_admin_cannot_set_password(self):
        self.client.force_authenticate(self.target)
        res = self.client.post(
            reverse("user-set-password", args=[self.target.pk]),
            {"new_password": "whatever-Pass!1"}, format="json",
        )
        self.assertEqual(res.status_code, 403)

    def test_admin_reads_user_api_key(self):
        res = self.client.get(reverse("user-get-user-api-key", args=[self.target.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertIn("key", res.data)

    def test_admin_regenerates_user_api_key(self):
        old = self.target.api_key.key
        res = self.client.post(reverse("user-admin-regenerate-api-key", args=[self.target.pk]))
        self.assertEqual(res.status_code, 200)
        self.target.api_key.refresh_from_db()
        self.assertNotEqual(self.target.api_key.key, old)


class RequestPasswordCodeErrorTests(APITestCase):
    def test_no_email_user_gets_400(self):
        # start_password_change raises when the user has no email to send to.
        u = User.objects.create_user(username="noemail_pw", password="x")  # no email
        self.client.force_authenticate(u)
        res = self.client.post(reverse("user-request-password-code"))
        self.assertEqual(res.status_code, 400)


class ApiKeyViewSetTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", password="x", is_staff=True, is_superuser=True
        )
        self.user = User.objects.create_user(username="owner", password="x")
        self.key, _ = APIKey.objects.get_or_create(user=self.user)
        self.client.force_authenticate(self.admin)

    def test_non_admin_forbidden(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(reverse("api-key-list")).status_code, 403)

    def test_regenerate_rotates_key(self):
        old = self.key.key
        res = self.client.post(reverse("api-key-regenerate", args=[self.key.pk]))
        self.assertEqual(res.status_code, 200)
        self.key.refresh_from_db()
        self.assertNotEqual(self.key.key, old)

    def test_revoke_then_activate(self):
        res = self.client.post(reverse("api-key-revoke", args=[self.key.pk]))
        self.assertEqual(res.status_code, 200)
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)

        res = self.client.post(reverse("api-key-activate", args=[self.key.pk]))
        self.assertEqual(res.status_code, 200)
        self.key.refresh_from_db()
        self.assertTrue(self.key.is_active)


class PublicRegisterVerifyEdgeTests(APITestCase):
    def test_register_reports_send_failed(self):
        with mock.patch(_SEND, side_effect=RuntimeError("down")):
            res = self.client.post(
                reverse("register-public"),
                {"username": "sig", "email": "sig@x.com",
                 "password": "Str0ng-Passw0rd!", "password_confirm": "Str0ng-Passw0rd!"},
                format="json",
            )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["email_verification"], "send_failed")

    def test_verify_email_via_get_activates(self):
        u = User.objects.create_user(username="ver", email="v@x.com", password="x")
        u.is_active = False
        u.save()
        uid = urlsafe_base64_encode(force_bytes(str(u.pk)))
        token = email_verification_token.make_token(u)
        res = self.client.get(reverse("verify-email-public"), {"uid": uid, "token": token})
        self.assertEqual(res.status_code, 200)
        u.refresh_from_db()
        self.assertTrue(u.is_active)

    def test_verify_email_missing_params_returns_400(self):
        self.assertEqual(self.client.post(reverse("verify-email-public"), {}, format="json").status_code, 400)
