"""Shared validation for uploaded knowledge files."""

from __future__ import annotations

from django.conf import settings


def validate_upload_size(uploaded_file) -> None:
    """Reject files larger than ``MAX_UPLOAD_SIZE_MB``.

    Raises ``ValueError`` with a human-readable message so callers can map it
    to a 400 response. Ingestion is synchronous, so this guards request time,
    embedding cost, and memory.
    """
    max_mb = float(getattr(settings, "MAX_UPLOAD_SIZE_MB", 20) or 0)
    if max_mb <= 0:
        return  # 0 / unset disables the limit

    size = getattr(uploaded_file, "size", 0) or 0
    max_bytes = int(max_mb * 1024 * 1024)
    if size > max_bytes:
        actual_mb = size / (1024 * 1024)
        raise ValueError(
            f"File is too large ({actual_mb:.1f} MB). "
            f"Maximum allowed size is {max_mb:g} MB."
        )
