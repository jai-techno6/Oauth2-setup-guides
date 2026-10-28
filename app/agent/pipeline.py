"""The live guide-generation pipeline.

resolve -> research (3 parallel web-search passes) -> synthesize (strict JSON schema)
        -> probe (live HTTP checks) -> Composio catalog check (optional)
        -> verify (evidence rules + quote checks) -> save

Each stage emits progress events so the UI can show the agent working.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit

import httpx

from ..config import Settings
from ..models import Guide, Probe, Source, slugify
from . import composio
from .llm import LLM
from .netsafe import FetchResult, make_client, safe_get
from .probe import run_probes
from .research import TOPICS, is_real_url, ResearchResult, research_topic
from .synthesize import build_sources, synthesize, to_guide
from .verify import check_quotes, enforce_rules

Emit = Callable[..., Awaitable[None]]

_domain_re = re.compile(r"^(https?://)?([a-z0-9-]+\.)+[a-z]{2,}(/\S*)?$", re.I)
_ctrl_re = re.compile(r"[\x00-\x1f\x7f]")


class PipelineError(RuntimeError):
    pass


def normalize_query(raw: str) -> tuple[str, str]:
    """Return (app_name, app_url) from free text: 'Notion', 'notion.so', or 'https://www.notion.so/'."""
    query = _ctrl_re.sub(" ", raw or "").strip()
    if len(query) < 2 or len(query) > 120:
        raise ValueError("Enter an app name or website (2-120 characters).")
    if " " not in query and _domain_re.match(query):
        url = query if query.lower().startswith("http") else "https://" + query
        host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
        labels = host.split(".")
        core = labels[-2] if len(labels) >= 2 else labels[0]
        if core in ("co", "com", "ac", "gov") and len(labels) >= 3:
            core = labels[-3]
        return core.capitalize(), f"https://{host}"
    return query, ""


async def run_pipeline(query: str, settings: Settings, emit: Emit, *, llm: Optional[LLM] = None,
                       http: Optional[httpx.AsyncClient] = None) -> Guide:
    app_name, app_url = normalize_query(query)
    app_label = f"{app_name} ({app_url})" if app_url else app_name
    llm = llm or LLM(settings.openai_api_key, settings.openai_model, settings.reasoning_effort, settings.web_search_tool)
    own_http = http is None
    http = http or make_client()
    log: list[str] = []
    tasks: list[asyncio.Task] = []
    composio_task: Optional[asyncio.Task] = None

    async def step(stage: str, message: str, **extra: Any) -> None:
        log.append(f"[{stage}] {message}")
        await emit("step", stage=stage, message=message, **extra)

    try:
        await step("resolve", f"Looking up {app_label}")
        composio_task = asyncio.create_task(composio.lookup(settings.composio_api_key, app_name)) \
            if settings.composio_api_key else None

        # ---- research: parallel focused passes, reported as each finishes ----
        await step("research", f"Researching {len(TOPICS)} topics in parallel with web search")
        tasks = [asyncio.create_task(research_topic(llm, app_label, t)) for t in TOPICS]
        results: list[ResearchResult] = []
        for fut in asyncio.as_completed(tasks):
            res = await fut
            results.append(res)
            if res.error:
                await step("research", f"{res.title}: failed ({res.error[:160]})", ok=False)
            else:
                shown = ", ".join(_host(u) for u, _ in res.cited[:4]) or "no citations"
                await step("research", f"{res.title}: {len(res.cited)} cited page(s) from {shown}",
                           queries=res.queries[:4])
        results.sort(key=lambda r: [t[0] for t in TOPICS].index(r.topic))
        good = [r for r in results if not r.error and r.notes.strip()]
        if not good:
            raise PipelineError("All research passes failed: " + "; ".join(r.error for r in results)[:500])

        official: set[str] = set()
        for r in good:
            official.update(d for d in r.official_domains if d)
        if app_url:
            official.add((urlsplit(app_url).hostname or "").removeprefix("www."))
        today = date.today().isoformat()
        sources = build_sources(good, official, today)
        n_off = sum(1 for s in sources if s.kind == "official")
        await step("sources", f"Collected {len(sources)} sources ({n_off} official, {len(sources) - n_off} other)")

        # ---- synthesize ----
        await step("synthesize", "Writing the guide into the fixed schema (difficulty, time, steps, blockers)")
        data = await synthesize(llm, app_label, query, good, sources)
        base = slugify(data.get("app_name") or app_name)
        guide = to_guide(data, slug=f"{base}-{secrets.token_hex(3)}", query=query, model=llm.model,
                         sources=sources, generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        if not guide.app_url and app_url:
            guide.app_url = app_url
        guide.aliases = sorted({app_name.lower(), (guide.app_name or "").lower()} - {""})
        for field in ("authorization_url", "token_url", "developer_console_url", "docs_url"):
            if getattr(guide.endpoints, field) and not is_real_url(getattr(guide.endpoints, field)):
                setattr(guide.endpoints, field, "")
        for phase in guide.phases:
            for s in phase.steps:
                if s.url and not is_real_url(s.url):
                    s.url = ""
        # The guide's own "unknowns" are already de-duplicated and written for the reader. The raw research
        # notes repeat each other across passes, so they are only a fallback when the guide lists none.
        if not guide.unknowns:
            for r in good:
                for c in [ln.strip("- ").strip() for ln in _section(r.notes, "UNKNOWNS")]:
                    if c and c.lower() != "none" and c not in guide.unknowns and len(guide.unknowns) < 8:
                        guide.unknowns.append(c)
        enforce_rules(guide)
        await step("synthesize", f"Draft ready: tier '{guide.difficulty}', {sum(len(p.steps) for p in guide.phases)} steps, "
                                 f"{len(guide.blockers)} blocker(s)")

        # ---- probes ----
        await step("probe", "Testing endpoints and the developer console without credentials")
        cache: dict[tuple[str, bool], FetchResult] = {}

        async def fetch(url: str, follow: bool = True) -> FetchResult:
            key = (url, follow)
            if key not in cache:
                cache[key] = await safe_get(http, url, follow=follow)
            return cache[key]

        async def on_probe(p: Probe) -> None:
            await step("probe", f"{p.check}: {p.outcome.upper()} - {p.result[:180]}", outcome=p.outcome)

        await run_probes(guide, fetch, on_probe)
        if not guide.probes:
            await step("probe", "No endpoint or console URL found to test")

        # ---- Composio catalog ----
        if composio_task is not None:
            cres = await composio_task
            if cres is not None:
                if cres.url:
                    sid = max((s.id for s in guide.sources), default=0) + 1
                    guide.sources.append(Source(id=sid, url=cres.url, title="Composio toolkit API",
                                                kind="probe", accessed=today))
                    guide.composio.claim.source_ids = [sid]
                guide.composio.status = cres.status  # type: ignore[assignment]
                guide.composio.claim.text = cres.text
                guide.composio.claim.evidence = "tested"
                guide.composio.claim.quote = ""
                guide.composio.claim.quote_check = "no_quote"
                guide.composio.claim.note = "Checked live against Composio's toolkit API."
                await step("composio", cres.text)

        # ---- verify ----
        await step("verify", "Checking quotes against the cited pages")
        counts = await check_quotes(guide, fetch)
        await step("verify", f"Quotes verified: {counts['verified']}, not found (downgraded): {counts['not_found']}, "
                             f"page unreadable: {counts['not_checked']}")
        ev = guide.evidence_counts()
        await step("done", f"Evidence mix: {ev['tested']} tested, {ev['cited']} cited, {ev['inferred']} inferred, "
                           f"{ev['unknown']} unknown. Confidence: {guide.confidence()}")
        guide.pipeline_log = log
        return guide
    finally:
        for t in [*tasks, composio_task]:
            if t is not None and not t.done():
                t.cancel()
        if own_http:
            await http.aclose()


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "?").removeprefix("www.")
    except ValueError:
        return "?"


def _section(notes: str, name: str) -> list[str]:
    out: list[str] = []
    active = False
    for line in notes.splitlines():
        s = line.strip()
        if s.upper().startswith(name + ":"):
            active = True
            continue
        if active and re.match(r"^[A-Z_]+:", s):
            break
        if active and s.startswith("-"):
            out.append(s)
    return out
