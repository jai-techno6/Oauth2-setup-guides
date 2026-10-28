"""Probe step: checks the agent actually runs against the provider (the "tested" evidence).

This is the lightweight stand-in for a browser agent. It does what an agent can do without the
developer's credentials and records where it gets stopped:
  - OAuth / OpenID discovery documents (real endpoints, scopes, PKCE support)
  - Authorization endpoint liveness
  - Developer console: does it redirect to a login page, or block automated clients?
Those stopping points are real data for the "where you take over" section.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from ..models import Conflict, Guide, Probe
from .netsafe import FetchResult

Fetcher = Callable[..., Awaitable[FetchResult]]

LOGIN_MARKERS = ("login", "signin", "sign-in", "sign_in", "servicelogin", "/auth", "accounts.", "sso", "oauth/authorize")
METHOD = "HTTP probe by the agent (no credentials)"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _looks_like_login(url: str) -> bool:
    low = url.lower()
    return any(marker in low for marker in LOGIN_MARKERS)


async def probe_discovery(fetch: Fetcher, hosts: list[str]) -> tuple[list[Probe], dict]:
    probes: list[Probe] = []
    for host in hosts:
        for path in ("/.well-known/openid-configuration", "/.well-known/oauth-authorization-server"):
            url = f"https://{host}{path}"
            res = await fetch(url)
            if res.ok:
                try:
                    doc = json.loads(res.body)
                except json.JSONDecodeError:
                    continue
                if isinstance(doc, dict) and doc.get("authorization_endpoint"):
                    scopes = doc.get("scopes_supported") or []
                    pkce = doc.get("code_challenge_methods_supported") or []
                    summary = (
                        f"Discovery document found. authorization_endpoint={doc.get('authorization_endpoint')}; "
                        f"token_endpoint={doc.get('token_endpoint', '?')}; "
                        f"scopes_supported lists {len(scopes)} scope(s)"
                        + (f" ({', '.join(map(str, scopes[:6]))}{'...' if len(scopes) > 6 else ''})" if scopes else "")
                        + (f"; PKCE methods: {', '.join(map(str, pkce))}" if pkce else "; PKCE methods not advertised")
                    )
                    probes.append(Probe(check="OAuth/OpenID discovery", url=url, outcome="pass", result=summary,
                                        checked_at=_now(), method=METHOD))
                    return probes, doc
    if hosts:
        probes.append(Probe(check="OAuth/OpenID discovery", url=f"https://{hosts[0]}/.well-known/openid-configuration",
                            outcome="info", result="No discovery document found on " + ", ".join(hosts) +
                            ". Many providers do not publish one; endpoints come from docs instead.",
                            checked_at=_now(), method=METHOD))
    return probes, {}


async def probe_authorize(fetch: Fetcher, url: str) -> Probe:
    res = await fetch(url, follow=False)
    if res.error:
        return Probe(check="Authorization endpoint reachable", url=url, outcome="error", result=res.error,
                     checked_at=_now(), method=METHOD)
    status = res.status
    if status in (403, 429):
        outcome, text = "blocked", f"HTTP {status}: the endpoint refused an automated client (bot protection)."
    elif status == 404:
        outcome, text = "fail", "HTTP 404: this URL does not look like a live authorization endpoint."
    elif status >= 500:
        outcome, text = "error", f"HTTP {status} from the server."
    else:
        outcome = "pass"
        text = f"HTTP {status} without parameters (expected: an error page or redirect), so the endpoint is live."
    return Probe(check="Authorization endpoint reachable", url=url, outcome=outcome, result=text,
                 checked_at=_now(), method=METHOD)


async def probe_console(fetch: Fetcher, url: str) -> Probe:
    res = await fetch(url)
    hops = res.hops[1:] if len(res.hops) > 1 else []
    login_hop = next((h for h in hops if _looks_like_login(h)), "")
    if res.error and not login_hop:
        return Probe(check="Developer console (where the agent stops)", url=url, outcome="error",
                     result=res.error, checked_at=_now(), method=METHOD)
    if login_hop:
        text = f"Redirected to a sign-in page ({_host(login_hop)}). A human must log in to create the app."
        outcome = "blocked"
    elif res.status in (401, 403, 429):
        text = f"HTTP {res.status}: the console blocks automated clients. A human needs a real browser session."
        outcome = "blocked"
    elif res.ok and any(w in res.body.lower() for w in ("sign in", "log in", "login", "captcha")):
        text = "Page loads but asks the visitor to sign in. A human must log in to create the app."
        outcome = "blocked"
    elif res.ok:
        text = f"HTTP {res.status}: page loads without sign-in (it may be a public landing page)."
        outcome = "info"
    else:
        text = f"HTTP {res.status}."
        outcome = "info"
    return Probe(check="Developer console (where the agent stops)", url=url, outcome=outcome, result=text,
                 checked_at=_now(), method=METHOD)


async def run_probes(guide: Guide, fetch: Fetcher, on_probe: Callable[[Probe], Awaitable[None]]) -> None:
    ep = guide.endpoints
    hosts: list[str] = []
    for url in (ep.authorization_url, ep.token_url, guide.app_url):
        h = _host(url)
        if h and h not in hosts:
            hosts.append(h)
    hosts = hosts[:3]

    discovery_probes, doc = await probe_discovery(fetch, hosts)
    for p in discovery_probes:
        guide.probes.append(p)
        await on_probe(p)

    if doc:
        disc_auth = str(doc.get("authorization_endpoint", ""))
        if ep.authorization_url and _host(ep.authorization_url) and disc_auth and \
                ep.authorization_url.rstrip("/") != disc_auth.rstrip("/"):
            guide.conflicts.append(Conflict(
                topic="Authorization endpoint",
                positions=f"Docs/research: {ep.authorization_url}. Discovery document: {disc_auth}.",
                resolution="The discovery document is machine-published by the provider; prefer it for sign-in "
                           "(OpenID) flows, but API-specific OAuth flows can use a different endpoint.",
            ))
        if not ep.authorization_url:
            ep.authorization_url = disc_auth
        if not ep.token_url and doc.get("token_endpoint"):
            ep.token_url = str(doc["token_endpoint"])

    if ep.authorization_url:
        p = await probe_authorize(fetch, ep.authorization_url)
        guide.probes.append(p)
        await on_probe(p)
        if p.outcome == "pass" or doc:
            ep.evidence = "tested"
            ep.note = "Authorization endpoint checked live by the agent."

    if ep.developer_console_url:
        p = await probe_console(fetch, ep.developer_console_url)
        guide.probes.append(p)
        await on_probe(p)
