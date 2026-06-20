"""Network safety helpers — primarily SSRF protection for outbound fetches.

The API-content sync fetches a tenant-supplied URL server-side. Without
validation that is a Server-Side Request Forgery (SSRF) vector: a caller could
point it at internal services (databases, admin panels) or the cloud metadata
endpoint (169.254.169.254) to steal credentials. ``validate_public_url`` blocks
non-public destinations before we ever connect.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURLError(ValueError):
    """Raised when a URL is not a safe, public http(s) destination."""


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local      # 169.254.0.0/16 — includes cloud metadata
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_public_url(url: str) -> str:
    """Return ``url`` if it is a safe public http(s) URL, else raise UnsafeURLError.

    Rejects non-http(s) schemes and any host that resolves to a private,
    loopback, link-local, reserved, or otherwise non-public address.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError("Only http and https URLs are allowed.")

    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL is missing a host.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError("Could not resolve the URL's host.") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_blocked_ip(ip):
            raise UnsafeURLError(
                "URL resolves to a private or reserved network address."
            )

    return url
