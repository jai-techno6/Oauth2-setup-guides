"""Research step: focused web-search passes that return quoted findings with URLs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .llm import LLM, output_text, web_sources

TOPICS: list[tuple[str, str, str]] = [
    (
        "registration",
        "App registration and OAuth basics",
        "How does a third-party developer register an OAuth app (client) for {app}? Find: the developer "
        "console URL, who can create an app (personal account, org admin, paid plan), required fields, "
        "redirect/callback URI rules (HTTPS, exact match, localhost), client types, the authorization and "
        "token endpoint URLs, how scopes work, token lifetimes and refresh tokens, PKCE.",
    ),
    (
        "approval",
        "Review, verification and distribution limits",
        "What must a developer complete before ARBITRARY external users (other companies, the public) can "
        "authorize an OAuth app for {app}? Find: app review, verification, marketplace/directory listing, "
        "partner programs, install caps or test-user limits before approval, required documents (privacy "
        "policy, demo video, security assessment, business/legal entity verification), and any review "
        "timelines the provider states. Note what works WITHOUT approval (testing mode, internal use).",
    ),
    (
        "access",
        "Paid tiers, restrictions and recent changes",
        "For {app}'s API used via OAuth: is a paid plan or paid API tier required, and what does it cost? "
        "Are there restricted scopes, rate limits for unapproved apps, or data-use policies that matter for "
        "AI agents? List changes announced in 2025-2026. Also check whether Composio (composio.dev) offers a "
        "Composio-managed OAuth app for {app} or requires developers to bring their own.",
    ),
]

INSTRUCTIONS = """You are a research agent helping developers set up OAuth apps with third-party providers.
Use web search. Prefer the provider's OFFICIAL developer documentation, changelogs and policy pages;
use third-party posts only when official docs are silent, and say so.

Return plain text in exactly this format:

OFFICIAL_DOMAINS: comma-separated domains that belong to the provider (e.g. slack.com, slack.dev)
OAUTH_SUPPORT: yes | no | partial | unknown  (does the provider let third-party developers create OAuth apps?)
FINDINGS:
- <one specific finding> | QUOTE: "<verbatim sentence fragment from the page, max 25 words>" | URL: <exact page URL>
- ...
CONFLICTS:
- <where sources disagree, with both URLs>   (or "- none")
UNKNOWNS:
- <what you could not find>   (or "- none")

Rules:
- 4 to 12 findings. Every finding needs a URL. Copy QUOTE text exactly from the page; if you cannot
  quote it, write QUOTE: "" and do not invent one.
- Never state a timeline, price, or limit unless a page says it. If unsure, put it under UNKNOWNS.
- If the app has no OAuth for third parties, say so and name the auth method it does offer.
- If the name is ambiguous, pick the most likely developer platform and say which one you chose."""

_url_re = re.compile(r"https?://[^\s\"'<>)\]|]+")


@dataclass
class ResearchResult:
    topic: str
    title: str
    notes: str = ""
    cited: list[tuple[str, str]] = field(default_factory=list)
    consulted: list[tuple[str, str]] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    official_domains: list[str] = field(default_factory=list)
    oauth_support: str = "unknown"
    error: str = ""


def clean_url(url: str) -> str:
    """Drop tracking params (OpenAI adds utm_source=openai) and fragments."""
    url = url.strip().rstrip(".,;:")
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def parse_notes(notes: str) -> tuple[list[str], str, list[str]]:
    domains: list[str] = []
    support = "unknown"
    for line in notes.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("OFFICIAL_DOMAINS:"):
            raw = stripped.split(":", 1)[1]
            domains = [d.strip().lower().lstrip("*.").removeprefix("www.") for d in raw.split(",") if d.strip()]
        elif upper.startswith("OAUTH_SUPPORT:"):
            value = stripped.split(":", 1)[1].strip().lower().split()[0:1]
            if value and value[0] in ("yes", "no", "partial", "unknown"):
                support = value[0]
    inline_urls = [clean_url(u) for u in _url_re.findall(notes)]
    return domains, support, inline_urls


async def research_topic(llm: LLM, app: str, topic: tuple[str, str, str]) -> ResearchResult:
    key, title, question = topic
    result = ResearchResult(topic=key, title=title)
    prompt = f"App: {app}\n\nQuestion: {question.format(app=app)}"
    try:
        data = await llm.research(INSTRUCTIONS, prompt)
    except Exception as exc:  # network/API errors are reported, not fatal for the other topics
        result.error = f"{type(exc).__name__}: {exc}"[:400]
        return result
    result.notes = output_text(data)
    cited, consulted, queries = web_sources(data)
    domains, support, inline = parse_notes(result.notes)
    result.official_domains = domains
    result.oauth_support = support
    result.queries = queries
    titles: dict[str, str] = {}
    for url, t in [(u, "") for u in inline] + [(clean_url(u), t) for u, t in cited]:
        if url not in titles or (t and not titles[url]):
            titles[url] = t
    result.cited = list(titles.items())
    result.consulted = [(clean_url(u), t) for u, t in consulted]
    return result
