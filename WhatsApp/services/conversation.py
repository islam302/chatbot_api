"""Domain logic for WhatsApp conversations.

Handles user/session lookup, message logging, and the routing of incoming
messages to either a built-in command handler or the RAG service.
"""

from __future__ import annotations

import logging
import time

from django.utils import timezone

from knowledge.services.rag import RagService, RagUnavailable

from ..models import (
    WhatsAppMessage,
    WhatsAppSession,
    WhatsAppUser,
)

logger = logging.getLogger(__name__)


WELCOME_MESSAGE = (
    "👋 Welcome! I'm your assistant. Send a question and I'll do my best to "
    "help.\n\nCommands:\n  /help — show this message\n  /lang <code> — set "
    "language preference (e.g. /lang en)"
)


def get_or_create_user(*, phone_number: str, profile_name: str = "") -> WhatsAppUser:
    user, created = WhatsAppUser.objects.get_or_create(
        phone_number=phone_number,
        defaults={"profile_name": profile_name},
    )
    if not created and profile_name and user.profile_name != profile_name:
        user.profile_name = profile_name
    user.update_last_message_time()
    return user


def get_or_create_session(user: WhatsAppUser) -> WhatsAppSession:
    session = (
        WhatsAppSession.objects.filter(user=user, is_active=True)
        .order_by("-updated_at")
        .first()
    )
    if session is None or session.is_expired():
        session = WhatsAppSession.objects.create(
            user=user,
            session_type=WhatsAppSession.SessionType.RAG_CHAT,
            expires_at=timezone.now() + timezone.timedelta(hours=24),
        )
    else:
        session.extend_session()
    return session


def log_message(
    *,
    user: WhatsAppUser,
    session: WhatsAppSession | None,
    message_type: str,
    text: str,
    whatsapp_message_id: str = "",
    response_time_ms: int | None = None,
    error_message: str = "",
) -> WhatsAppMessage:
    return WhatsAppMessage.objects.create(
        user=user,
        session=session,
        message_type=message_type,
        message_text=text,
        whatsapp_message_id=whatsapp_message_id,
        response_time_ms=response_time_ms,
        error_message=error_message,
    )


def handle_incoming_message(message_data: dict) -> tuple[str, WhatsAppUser, WhatsAppSession]:
    """Process an incoming text message, returning the reply to send back.

    Also persists incoming/outgoing messages and updates session/user state.
    """
    text = message_data["message_text"]
    user = get_or_create_user(
        phone_number=message_data["from_number"],
        profile_name=message_data.get("profile_name", ""),
    )
    session = get_or_create_session(user)
    user.increment_message_count()
    session.increment_message_count()

    log_message(user=user, session=session, message_type="incoming", text=text)

    started = time.monotonic()
    try:
        reply = _route(text, user)
        error_message = ""
    except Exception as exc:  # noqa: BLE001 — surfaced to operator via logs
        logger.exception("Error generating WhatsApp reply")
        reply = "Sorry, something went wrong. Please try again."
        error_message = str(exc)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    log_message(
        user=user,
        session=session,
        message_type="outgoing",
        text=reply,
        response_time_ms=elapsed_ms,
        error_message=error_message,
    )
    return reply, user, session


def _route(text: str, user: WhatsAppUser) -> str:
    lowered = text.lower().strip()
    if lowered in {"/start", "/help", "start", "help"}:
        return WELCOME_MESSAGE
    if lowered.startswith("/lang"):
        return _handle_language(text, user)

    try:
        result = RagService.instance().answer(text)
    except RagUnavailable:
        return (
            "I cannot search our knowledge base right now. Please try again later."
        )
    return result.answer


def _handle_language(text: str, user: WhatsAppUser) -> str:
    parts = text.split()
    if len(parts) < 2:
        choices = ", ".join(c.value for c in WhatsAppUser.LanguagePreference)
        return f"Available languages: {choices}\nUsage: /lang en"
    code = parts[1].strip().lower()
    valid = {c.value for c in WhatsAppUser.LanguagePreference}
    if code not in valid:
        return f"Unsupported language: {code}"
    user.language_preference = code
    user.save(update_fields=["language_preference", "updated_at"])
    return f"Language set to {code}."
