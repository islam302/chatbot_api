"""Account self-service flows that need more than a serializer — currently the
verified email change (send a code, then confirm it)."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import EmailChangeRequest, User


class EmailChangeError(Exception):
    """Raised when confirming an email change can't proceed (bad/expired code)."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def start_email_change(user: User, new_email: str) -> EmailChangeRequest:
    """Create a pending email change and email a 6-digit code to ``new_email``.

    Any earlier unused request for this user is invalidated so only the latest
    code works.
    """
    new_email = new_email.strip().lower()

    # Invalidate previous pending requests — one active code at a time.
    EmailChangeRequest.objects.filter(user=user, is_used=False).update(is_used=True)

    ttl = int(getattr(settings, "EMAIL_VERIFICATION_TTL_MINUTES", 15))
    code = EmailChangeRequest.generate_code()
    req = EmailChangeRequest(
        user=user,
        new_email=new_email,
        expires_at=timezone.now() + timedelta(minutes=ttl),
    )
    req.set_code(code)
    req.save()

    send_mail(
        subject="Confirm your new email address",
        message=(
            f"Hi {user.first_name or user.username},\n\n"
            f"Use this code to confirm your new email address:\n\n"
            f"    {code}\n\n"
            f"It expires in {ttl} minutes. If you didn't request this, ignore this email."
        ),
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[new_email],
        fail_silently=False,
    )
    return req


def confirm_email_change(user: User, code: str) -> User:
    """Validate ``code`` against the latest pending request and apply the change.

    Raises ``EmailChangeError`` on no pending request, expiry, too many attempts,
    or a wrong code.
    """
    req = (
        EmailChangeRequest.objects.filter(user=user, is_used=False)
        .order_by("-created_at")
        .first()
    )
    if req is None:
        raise EmailChangeError("No pending email change. Request a new code.")

    max_attempts = int(getattr(settings, "EMAIL_VERIFICATION_MAX_ATTEMPTS", 5))
    if req.is_expired:
        req.is_used = True
        req.save(update_fields=["is_used", "updated_at"])
        raise EmailChangeError("The code has expired. Request a new one.")
    if req.attempts >= max_attempts:
        req.is_used = True
        req.save(update_fields=["is_used", "updated_at"])
        raise EmailChangeError("Too many attempts. Request a new code.")

    if not req.code_matches(code):
        req.attempts += 1
        req.save(update_fields=["attempts", "updated_at"])
        raise EmailChangeError("Incorrect code.")

    # Guard against the address being taken since the request was made.
    if (
        User.objects.exclude(pk=user.pk)
        .filter(email__iexact=req.new_email)
        .exists()
    ):
        req.is_used = True
        req.save(update_fields=["is_used", "updated_at"])
        raise EmailChangeError("That email address is already in use.")

    with transaction.atomic():
        user.email = req.new_email
        user.email_verified = True
        user.save(update_fields=["email", "email_verified"])
        req.is_used = True
        req.save(update_fields=["is_used", "updated_at"])
    return user
