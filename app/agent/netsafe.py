"""Safe outbound HTTP for the agent.

URLs come from user input and model output, so every request (and every redirect hop) is
checked: http(s) only, ports 80/443 only, and the host must resolve to public IP addresses.
This blocks requests to localhost, private networks, and cloud metadata endpoints.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = (
    "Mozilla/5.0 (compatible; OAuthSetupGuideAgent/1.0; research bot for developer docs)"
)
MAX_BODY_BYTES = 2_000_000
MAX_REDIRECTS = 5


class UnsafeURL(ValueError):
    """Raised when a URL is not allowed for outbound requests."""


@dataclass
class FetchResult:
    url: str
    final_url: str = ""
    status: int = 0
    content_type: str = ""
    body: str = ""
    hops: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and 200 <= self.status < 300


def _ip_is_public(raw: str) -> bool:
    ip = ipaddress.ip_address(raw.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def assert_public_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURL(f"scheme not allowed: {parts.scheme or '(none)'}")
    host = parts.hostname
    if not host:
        raise UnsafeURL("missing host")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURL("invalid port") from exc
    if port not in (80, 443):
        raise UnsafeURL(f"port not allowed: {port}")
    if parts.username or parts.password:
        raise UnsafeURL("credentials in URL not allowed")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:  # gaierror is an OSError; bad IDNA names raise UnicodeError
        raise UnsafeURL(f"cannot resolve host {host}") from exc
    if not infos:
        raise UnsafeURL(f"cannot resolve host {host}")
    for info in infos:
        if not _ip_is_public(info[4][0]):
            raise UnsafeURL(f"host {host} resolves to a non-public address")


def make_client(timeout: float = 12.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.5"},
    )


async def safe_get(client: httpx.AsyncClient, url: str, *, follow: bool = True) -> FetchResult:
    """GET a URL, following redirects manually so each hop is checked."""
    result = FetchResult(url=url)
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            await assert_public_url(current)
        except UnsafeURL as exc:
            result.error = f"blocked by safety check: {exc}"
            result.final_url = current
            return result
        result.hops.append(current)
        try:
            async with client.stream("GET", current) as resp:
                result.status = resp.status_code
                result.content_type = resp.headers.get("content-type", "")
                location = resp.headers.get("location")
                if follow and resp.is_redirect and location:
                    current = urljoin(current, location)
                    continue
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_BODY_BYTES:
                        break
                raw = b"".join(chunks)
                try:
                    result.body = raw.decode(resp.encoding or "utf-8", errors="replace")
                except LookupError:  # unknown charset name in the Content-Type header
                    result.body = raw.decode("utf-8", errors="replace")
                result.final_url = current
                if not follow and location:
                    result.hops.append(urljoin(current, location))
                return result
        except Exception as exc:  # network errors, invalid URLs: report, never crash the pipeline
            result.error = f"{type(exc).__name__}: {exc}"[:300]
            result.final_url = current
            return result
    result.error = "too many redirects"
    result.final_url = current
    return result


_script_re = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.S | re.I)
_tag_re = re.compile(r"<[^>]+>")
_ws_re = re.compile(r"\s+")


def html_to_text(body: str) -> str:
    text = _script_re.sub(" ", body)
    text = _tag_re.sub(" ", text)
    text = html.unescape(text)
    return _ws_re.sub(" ", text).strip()
