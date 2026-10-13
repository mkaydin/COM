"""URL safety checks for anything the Scout fetches on the user's behalf.

Divination pulls URLs out of search results, so a hostile or merely
compromised result could otherwise make the backend reach into the private
network (cloud metadata endpoints, internal services, the local Postgres).
Every outbound fetch should go through `ensure_public_url` first.
"""

import ipaddress
import socket
from urllib.parse import urlparse

from logging_config import get_logger

logger = get_logger(__name__)

ALLOWED_SCHEMES = {"http", "https"}
_MAX_REDIRECTS = 5


class UnsafeURLError(ValueError):
    """Raised when a URL points somewhere we refuse to fetch."""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return True
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    # Cloud provider metadata endpoints.
    if str(ip) in {"169.254.169.254", "fd00:ec2::254"}:
        return True
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) would otherwise slip past.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None and _is_blocked_ip(mapped):
        return True
    return False


def ensure_public_url(url: str) -> str:
    """Validate `url` is an http(s) URL resolving to a public address.

    Returns the URL unchanged, or raises UnsafeURLError. Resolving the
    hostname here closes the DNS-rebinding window only partially — callers
    must still avoid following redirects into private space (see
    `safe_follow_redirects`).
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Blocked scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise UnsafeURLError("URL has no host")

    host = parsed.hostname
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        raise UnsafeURLError(f"Cannot resolve host {host!r}: {e}") from e

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise UnsafeURLError(f"Unparseable address for {host!r}: {addr!r}")
        if _is_blocked_ip(ip):
            raise UnsafeURLError(f"Blocked private address {ip} for host {host!r}")

    return url


def safe_follow_redirects(client, url: str, max_redirects: int = _MAX_REDIRECTS) -> str:
    """GET `url`, re-validating each redirect target against `ensure_public_url`.

    httpx's own redirect handling would follow a 302 straight into a private
    address without re-checking, so redirects are walked manually.
    """
    current = url
    for _ in range(max_redirects + 1):
        ensure_public_url(current)
        resp = client.get(current, follow_redirects=False)
        if resp.is_redirect:
            location = resp.headers.get("location")
            if not location:
                return current
            current = str(resp.url.join(location))
            continue
        return current
    raise UnsafeURLError(f"Too many redirects starting at {url!r}")
