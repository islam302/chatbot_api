import binascii
import os
import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class User(AbstractUser):
    """Custom user model with a UUID primary key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

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
