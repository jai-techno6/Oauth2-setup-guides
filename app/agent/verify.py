"""Verification step: enforce evidence rules and check quotes against the cited pages.

The model is not trusted to label its own evidence. After synthesis:
  1. Source ids that do not exist are dropped.
  2. "cited" with no valid source becomes "inferred".
  3. For "cited" claims with a quote, the cited page is fetched and the quote searched for.
     Found      -> quote_check="verified"
     Not found on a page with real text -> downgraded to "inferred", quote_check="not_found"
     Page unreachable or mostly JavaScript -> quote_check="not_checked" (label kept, flagged in UI)
"""

from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable, Optional

import httpx

from ..models import Evidenced, Guide
from .netsafe import FetchResult, html_to_text, safe_get

MIN_PAGE_TEXT = 1500  # below this the page is likely a JS shell or bot wall; don't judge quotes
MAX_PAGES = 14

_norm_re = re.compile(r"[^a-z0-9]+")
_QUOTE_CHARS = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u00a0": " "})


def normalize(text: str) -> str:
    return " ".join(_norm_re.sub(" ", text.translate(_QUOTE_CHARS).lower()).split())


def quote_in_page(quote: str, page_norm: str) -> bool:
    q = normalize(quote).strip(" .")
    if not q:
        return False
    if q in page_norm:
        return True
    # Tolerate small edits (ellipses, dropped words): any 6-word window must appear,
    # and most of the quote's words must be on the page.
    words = q.split()
    if len(words) < 6:
        return False
    page_words = set(page_norm.split())
    coverage = sum(1 for w in words if w in page_words) / len(words)
    if coverage < 0.85:
        return False
    for i in range(0, len(words) - 5):
        if " ".join(words[i : i + 6]) in page_norm:
            return True
    return False


def enforce_rules(guide: Guide) -> None:
    valid = {s.id for s in guide.sources}
    for item in guide.iter_evidenced():
        item.source_ids = [i for i in item.source_ids if i in valid]
        item.quote = (item.quote or "").strip().strip('"').strip()
        if item.evidence == "tested" and guide.origin == "agent":
            item.evidence = "inferred"
            item.note = _append(item.note, "Model labelled this tested; only code-run checks count as tested.")
        if item.evidence == "cited" and not item.source_ids:
            item.evidence = "inferred"
            item.note = _append(item.note, "No valid source was attached, so this is treated as inferred.")
        if item.evidence != "cited":
            item.quote_check = "no_quote" if not item.quote else "not_checked"
        elif not item.quote:
            item.quote_check = "no_quote"
        else:
            item.quote_check = "not_checked"
    for conflict in guide.conflicts:
        conflict.source_ids = [i for i in conflict.source_ids if i in valid]


def _append(note: str, extra: str) -> str:
    return f"{note} {extra}".strip() if note else extra


Fetcher = Callable[[str], Awaitable[FetchResult]]


async def check_quotes(guide: Guide, fetch: Fetcher) -> dict[str, int]:
    """Fetch cited pages once each and check quotes. Returns counts for the progress log."""
    targets: list[Evidenced] = [i for i in guide.iter_evidenced() if i.evidence == "cited" and i.quote]
    needed: list[int] = []
    for item in targets:
        for sid in item.source_ids[:2]:
            if sid not in needed:
                needed.append(sid)
    needed = needed[:MAX_PAGES]

    async def load(sid: int) -> tuple[int, Optional[str]]:
        src = guide.source(sid)
        if src is None:
            return sid, None
        res = await fetch(src.url)
        if not res.ok:
            return sid, None
        text = html_to_text(res.body) if "html" in res.content_type or "<" in res.body[:200] else res.body
        if len(text) < MIN_PAGE_TEXT:
            return sid, None
        return sid, normalize(text)

    pages = dict(await asyncio.gather(*(load(s) for s in needed)))
    counts = {"verified": 0, "not_found": 0, "not_checked": 0}
    for item in targets:
        checked_any = False
        found = False
        for sid in item.source_ids[:2]:
            page = pages.get(sid)
            if page is None:
                continue
            checked_any = True
            if quote_in_page(item.quote, page):
                found = True
                break
        if found:
            item.quote_check = "verified"
        elif checked_any:
            item.quote_check = "not_found"
            item.evidence = "inferred"
            item.note = _append(item.note, "Quote not found on the cited page; downgraded from cited to inferred.")
        else:
            item.quote_check = "not_checked"
            item.note = _append(item.note, "Cited page could not be read automatically (blocked or JavaScript-only).")
        counts[item.quote_check] += 1
    return counts


def make_fetcher(client: httpx.AsyncClient) -> Fetcher:
    cache: dict[str, FetchResult] = {}

    async def fetch(url: str) -> FetchResult:
        if url not in cache:
            cache[url] = await safe_get(client, url)
        return cache[url]

    return fetch
