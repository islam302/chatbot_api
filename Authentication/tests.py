"""User self-service settings: profile edit, verified email change, API-key rules."""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from Authentication.models import EmailChangeRequest, User

FIXED_CODE = "123456"
_GEN = "Authentication.models.EmailChangeRequest.generate_code"


class ProfileEditTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", password="Test!Pass2026", email="alice@old.com", first_name="Ali"
        )
        self.client.force_authenticate(user=self.user)

    def test_me_returns_current_user(self):
        res = self.client.get(reverse("user-me"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["username"], "alice")

    def test_update_name_and_username(self):
        res = self.client.patch(
            reverse("user-me"),
            {"first_name": "Alice", "last_name": "Smith", "username": "alice2"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Alice")
        self.assertEqual(self.user.last_name, "Smith")
        self.assertEqual(self.user.username, "alice2")

    def test_duplicate_username_rejected(self):
        User.objects.create_user(username="bob", password="x")
        res = self.client.patch(reverse("user-me"), {"username": "bob"}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_patch_me_cannot_change_email_directly(self):
        # Email is not a profile field — it can only change via the verified flow.
        res = self.client.patch(
            reverse("user-me"), {"email": "sneaky@x.com"}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "alice@old.com")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailChangeTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", password="x", email="alice@old.com"
        )
        self.client.force_authenticate(user=self.user)

    def _request_change(self, new_email="new@x.com"):
        return self.client.post(
            reverse("user-change-email"), {"new_email": new_email}, format="json"
        )

    @mock.patch(_GEN, return_value=FIXED_CODE)
    def test_change_email_sends_code_and_defers_update(self, _):
        res = self._request_change()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["new@x.com"])
        self.assertIn(FIXED_CODE, mail.outbox[0].body)
        # Email NOT changed until verified.
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "alice@old.com")
        self.assertTrue(
            EmailChangeRequest.objects.filter(user=self.user, is_used=False).exists()
        )

    @mock.patch(_GEN, return_value=FIXED_CODE)
    def test_verify_applies_the_change(self, _):
        self._request_change()
        res = self.client.post(
            reverse("user-verify-email"), {"code": FIXED_CODE}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@x.com")

    @mock.patch(_GEN, return_value=FIXED_CODE)
    def test_wrong_code_rejected_and_email_unchanged(self, _):
        self._request_change()
        res = self.client.post(
            reverse("user-verify-email"), {"code": "000000"}, format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "alice@old.com")

    @mock.patch(_GEN, return_value=FIXED_CODE)
    def test_expired_code_rejected(self, _):
        self._request_change()
        req = EmailChangeRequest.objects.get(user=self.user, is_used=False)
        req.expires_at = timezone.now() - timedelta(minutes=1)
        req.save(update_fields=["expires_at"])
        res = self.client.post(
            reverse("user-verify-email"), {"code": FIXED_CODE}, format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "alice@old.com")

    def test_verify_without_request_rejected(self):
        res = self.client.post(
            reverse("user-verify-email"), {"code": "123456"}, format="json"
        )
        self.assertEqual(res.status_code, 400)

    def test_email_already_in_use_rejected(self):
        User.objects.create_user(username="bob", password="x", email="taken@x.com")
        res = self._request_change("taken@x.com")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CreateUserVerificationTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", password="x", is_staff=True, is_superuser=True
        )
        self.client.force_authenticate(user=self.admin)

    def _create(self, **overrides):
        body = {
            "username": "newbie",
            "email": "newbie@example.com",
            "first_name": "New",
            "last_name": "Bie",
            "password": "Str0ng-Passw0rd!",
            "password_confirm": "Str0ng-Passw0rd!",
        }
        body.update(overrides)
        return self.client.post(reverse("user-list"), body, format="json")

    @mock.patch(_GEN, return_value=FIXED_CODE)
    def test_create_emails_code_and_marks_unverified(self, _):
        res = self._create()
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["email_verification"], "code_sent")
        self.assertFalse(res.data["email_verified"])
        # A code was emailed to the new user's address.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["newbie@example.com"])
        self.assertIn(FIXED_CODE, mail.outbox[0].body)
        user = User.objects.get(username="newbie")
        self.assertFalse(user.email_verified)

    @mock.patch(_GEN, return_value=FIXED_CODE)
    def test_new_user_can_verify_their_email(self, _):
        self._create()
        newbie = User.objects.get(username="newbie")
        # The new user logs in and confirms with the code from their inbox.
        self.client.force_authenticate(user=newbie)
        res = self.client.post(
            reverse("user-verify-email"), {"code": FIXED_CODE}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        newbie.refresh_from_db()
        self.assertTrue(newbie.email_verified)

    def test_create_without_email_sends_nothing(self):
        res = self._create(email="")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["email_verification"], "not_sent")
        self.assertEqual(len(mail.outbox), 0)


class ApiKeyImmutabilityTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")
        self.client.force_authenticate(user=self.user)

    def test_user_can_view_own_key(self):
        res = self.client.get(reverse("user-api-key"))
        self.assertEqual(res.status_code, 200)
        self.assertIn("key", res.data)

    def test_self_service_key_rotation_removed(self):
        # Users must NOT be able to rotate their key. The self-service endpoint is
        # gone, so a non-admin request is denied (never 2xx) and the key is unchanged.
        from Authentication.models import APIKey

        key_before = APIKey.objects.get_or_create(user=self.user)[0].key
        res = self.client.post("/api/v1/users/regenerate-api-key/")
        self.assertGreaterEqual(res.status_code, 400)  # denied, not performed
        self.assertEqual(APIKey.objects.get(user=self.user).key, key_before)
