"""Synthesis step: turn research notes into the fixed guide schema (strict JSON schema output)."""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import urlsplit

from ..models import Guide, Source
from .llm import LLM
from .research import ResearchResult

EVIDENCE_LLM = ["cited", "inferred", "unknown"]  # "tested" is reserved for checks the code runs


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_S = {"type": "string"}
_IDS = {"type": "array", "items": {"type": "integer"}}
_EVIDENCE = {"type": "string", "enum": EVIDENCE_LLM}
_CLAIM_PROPS = {"text": _S, "evidence": _EVIDENCE, "source_ids": _IDS, "quote": _S}
_ITEM_PROPS = {"title": _S, "detail": _S, "evidence": _EVIDENCE, "source_ids": _IDS, "quote": _S}

CLAIM = _obj(_CLAIM_PROPS)
ITEM = _obj(_ITEM_PROPS)
STEP = _obj({**_ITEM_PROPS, "who": {"type": "string", "enum": ["agent", "human", "either"]}, "why_human": _S, "url": _S})
BLOCKER = _obj({**_ITEM_PROPS, "workaround": _S})

GUIDE_SCHEMA: dict[str, Any] = _obj(
    {
        "app_name": _S,
        "app_url": _S,
        "summary": _S,
        "oauth_support": {"type": "string", "enum": ["yes", "no", "partial", "unknown"]},
        "oauth_support_claim": CLAIM,
        "difficulty": {"type": "string", "enum": ["trivial", "moderate", "hard", "gated", "no_oauth", "unknown"]},
        "difficulty_claim": CLAIM,
        "composio_status": {"type": "string", "enum": ["managed", "byo_required", "unknown"]},
        "composio_claim": CLAIM,
        "endpoints": _obj({"developer_console_url": _S, "docs_url": _S, "authorization_url": _S, "token_url": _S}),
        "time": _obj({"hands_on": CLAIM, "approval_wait": CLAIM, "fastest_path": CLAIM}),
        "phases": {
            "type": "array",
            "items": _obj({"name": _S, "summary": _S, "steps": {"type": "array", "items": STEP}}),
        },
        "requirements": {"type": "array", "items": ITEM},
        "gotchas": {"type": "array", "items": ITEM},
        "blockers": {"type": "array", "items": BLOCKER},
        "agent_can": {"type": "array", "items": _S},
        "human_must": {"type": "array", "items": _S},
        "conflicts": {
            "type": "array",
            "items": _obj({"topic": _S, "positions": _S, "resolution": _S, "source_ids": _IDS}),
        },
        "unknowns": {"type": "array", "items": _S},
    }
)

INSTRUCTIONS = """You write OAuth setup guides for developers building AI agents. The developer must register
their own OAuth app with a provider (client ID/secret, redirect URI, scopes, consent screen) so their end
users can connect accounts. Your guide tells them how hard that is, how long it takes including approval
waits, what can block them, and which steps an agent can do versus where a human must take over.

Evidence rules (most important):
- Use ONLY the research notes and numbered sources given. Do not add facts from memory unless you label
  them "inferred".
- evidence="cited" requires source_ids pointing at sources that state the claim. Put a short verbatim
  quote (max 25 words) from the notes' QUOTE fields in "quote" when one exists; otherwise "".
- evidence="inferred" for your own reasoning (e.g. hands-on minutes estimated from the number of steps).
- evidence="unknown" when the notes do not say. Never invent timelines, prices, or limits.
- If sources disagree, add a "conflicts" entry and do not pick a side silently.

Difficulty tiers (pick by the hardest step needed for production use by external users):
- trivial: self-serve form, credentials in minutes, no review for external users.
- moderate: self-serve, but install caps or a listing/marketplace review before scaling.
- hard: provider verification/review or security assessment before broad use; weeks.
- gated: useful access needs partner approval, a registered legal entity, or a paid API tier.
- no_oauth: provider has no third-party OAuth (say what auth it offers in summary and blockers).
- unknown: not enough evidence.

Content rules:
- summary: 2-3 plain sentences: tier, the main time sink, and the first thing to do.
- phases: 2-4 phases (e.g. "Create the app", "Test with your own account", "Get approved for production").
  Each step says who does it. "agent" = can be automated without the developer's credentials (reading docs,
  drafting text, generating config, checking endpoints). "human" = needs login, MFA, CAPTCHA, payment,
  legal/business documents, a demo video, or accepting terms. Fill why_human for human steps.
- time.hands_on: developer effort (e.g. "20-40 minutes"). time.approval_wait: waiting on the provider
  ("none", or the stated review time). time.fastest_path: how to start using it before approval, if possible.
- requirements: hard prerequisites. gotchas: things that commonly break setups (redirect URI matching,
  token expiry, test-user caps, scope review). blockers: things that can stop launch, with a workaround.
- composio_status: "managed" only if a source says Composio provides a managed OAuth app for this app;
  "byo_required" only if a source says you must bring your own; otherwise "unknown".
- endpoints: copy URLs only if they appear in the notes; else "".
- Keep each detail under 60 words. Write for a developer who will forward this guide to a teammate."""


def build_sources(results: list[ResearchResult], official_domains: set[str], today: str) -> list[Source]:
    """Number the sources. Cited sources first; consulted-only sources fill up to a cap."""
    ordered: dict[str, str] = {}
    for res in results:
        for url, title in res.cited:
            if url.startswith("http") and (url not in ordered or (title and not ordered[url])):
                ordered[url] = title
    for res in results:
        for url, title in res.consulted:
            if len(ordered) >= 30:
                break
            if url.startswith("http") and url not in ordered:
                ordered[url] = title
    sources = []
    for i, (url, title) in enumerate(list(ordered.items())[:30], start=1):
        sources.append(Source(id=i, url=url, title=title or "", kind=classify(url, official_domains), accessed=today))
    return sources


def classify(url: str, official_domains: set[str]) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host.endswith("composio.dev"):
        return "composio"
    for dom in official_domains:
        if dom and (host == dom or host.endswith("." + dom)):
            return "official"
    return "third_party"


def build_prompt(app: str, query: str, results: list[ResearchResult], sources: list[Source]) -> str:
    lines = [f"Today: {date.today().isoformat()}", f"Developer asked about: {query}", f"Resolved app: {app}", "", "SOURCES:"]
    for src in sources:
        label = {"official": "official", "third_party": "third-party", "composio": "Composio"}.get(src.kind, src.kind)
        lines.append(f"[{src.id}] ({label}) {src.title or '(untitled)'} - {src.url}")
    lines.append("")
    for res in results:
        lines.append(f"=== RESEARCH NOTES: {res.title} ===")
        lines.append(res.notes.strip() if res.notes else f"(no notes: {res.error or 'empty'})")
        lines.append("")
    lines.append("Map every URL in the notes to its [id] above when filling source_ids.")
    return "\n".join(lines)


async def synthesize(llm: LLM, app: str, query: str, results: list[ResearchResult], sources: list[Source]) -> dict:
    prompt = build_prompt(app, query, results, sources)
    return await llm.structured(INSTRUCTIONS, prompt, "oauth_setup_guide", GUIDE_SCHEMA)


def to_guide(data: dict, *, slug: str, query: str, model: str, sources: list[Source], generated_at: str) -> Guide:
    """Map the model's JSON onto the Guide model."""
    payload = dict(data)
    payload["composio"] = {"status": payload.pop("composio_status", "unknown"), "claim": payload.pop("composio_claim", {})}
    payload.update(
        slug=slug,
        origin="agent",
        query=query,
        model=model,
        generated_at=generated_at,
        sources=[s.model_dump() for s in sources],
    )
    return Guide.model_validate(payload)
