import binascii
import hashlib
import os
import secrets
import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class User(AbstractUser):
    """Custom user model with a UUID primary key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # True once the user has confirmed control of their email via a code. Set on
    # account creation to False; flipped by the verify-email flow.
    email_verified = models.BooleanField(default=False)

    class Meta:
        db_table = "auth_user"


class APIKey(TimestampedModel):
    """Per-user API key for authentication and multi-tenancy."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_key",
    )
    key = models.CharField(max_length=40, unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # Keep the original table + index name so existing data is preserved.
        db_table = "knowledge_apikey"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["key", "is_active"], name="knowledge_a_key_37da01_idx")]

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
        return super().save(*args, **kwargs)

    @staticmethod
    def generate_key():
        return binascii.hexlify(os.urandom(20)).decode()

    def __str__(self):
        return f"{self.user.username} - {self.key[:8]}..."


def _hash_code(code: str) -> str:
    """SHA-256 of a verification code — we never store the raw code."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class EmailChangeRequest(TimestampedModel):
    """A pending email change awaiting confirmation by a code sent to the NEW address.

    The user's email is only updated once they submit the matching code, so an
    address can't be set without proving control of it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_change_requests",
    )
    new_email = models.EmailField()
    # Store only the hash of the 6-digit code, never the code itself.
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "is_used"])]

    @staticmethod
    def generate_code() -> str:
        """A 6-digit numeric code (zero-padded)."""
        return f"{secrets.randbelow(1_000_000):06d}"

    def set_code(self, raw_code: str) -> None:
        self.code_hash = _hash_code(raw_code)

    def code_matches(self, raw_code: str) -> bool:
        return secrets.compare_digest(self.code_hash, _hash_code(raw_code or ""))

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def __str__(self):
        return f"EmailChange({self.user_id} -> {self.new_email})"
