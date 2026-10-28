"""Optional live check of Composio's toolkit catalog (needs COMPOSIO_API_KEY).

GET https://backend.composio.dev/api/v3/toolkits/{slug} returns `composio_managed_auth_schemes`.
If it contains OAUTH2 the developer can skip creating their own OAuth app while prototyping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import httpx

API = "https://backend.composio.dev/api/v3/toolkits/"
DOCS = "https://docs.composio.dev/toolkits/managed-auth"


@dataclass
class ComposioResult:
    status: str  # managed | byo_required | unknown
    text: str
    slug: str = ""
    url: str = ""


def candidate_slugs(app_name: str) -> list[str]:
    base = app_name.lower().strip()
    out: list[str] = []
    for cand in (re.sub(r"[^a-z0-9]", "", base), re.sub(r"[^a-z0-9]+", "_", base).strip("_"),
                 re.sub(r"[^a-z0-9]", "", base.split()[0]) if base.split() else ""):
        if cand and cand not in out:
            out.append(cand)
    return out[:3]


async def lookup(api_key: str, app_name: str, client: Optional[httpx.AsyncClient] = None) -> Optional[ComposioResult]:
    if not api_key:
        return None
    own = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        for slug in candidate_slugs(app_name):
            try:
                resp = await client.get(API + slug, headers={"x-api-key": api_key})
            except httpx.HTTPError:
                return None
            if resp.status_code == 404:
                continue
            if resp.status_code != 200:
                return None
            data = resp.json()
            managed = [str(s).lower() for s in (data.get("composio_managed_auth_schemes") or [])]
            modes = [str(d.get("mode", "")).lower() for d in (data.get("auth_config_details") or []) if isinstance(d, dict)]
            name = data.get("name") or slug
            if "oauth2" in managed or "oauth1" in managed:
                return ComposioResult("managed", f"Composio's catalog lists a Composio-managed OAuth app for {name} "
                                      f"(toolkit '{slug}'). You only need your own OAuth app for your branding, "
                                      "custom scopes, or a dedicated rate-limit quota.", slug, API + slug)
            if any(m.startswith("oauth") for m in modes):
                return ComposioResult("byo_required", f"Composio's catalog has {name} (toolkit '{slug}') with OAuth "
                                      "but no Composio-managed app. You must register your own OAuth app.", slug, API + slug)
            return ComposioResult("unknown", f"Composio's catalog has {name} (toolkit '{slug}') but lists no OAuth "
                                  f"method (auth modes: {', '.join(modes) or 'none listed'}).", slug, API + slug)
        return ComposioResult("unknown", f"No Composio toolkit found for slugs {', '.join(candidate_slugs(app_name))}.")
    finally:
        if own:
            await client.aclose()
