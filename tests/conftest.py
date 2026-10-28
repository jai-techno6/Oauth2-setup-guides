"""Shared fixtures. Tests never call OpenAI or the internet: the LLM and HTTP layers are faked."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.agent import netsafe
from app.config import APP_DIR, Settings

DOC_PAGE = (
    "<html><body><h1>Acme OAuth</h1>"
    + "<p>Filler text about the Acme developer platform. </p>" * 60
    + "<p>All apps that request the acme.write scope must pass app review before external users can authorize.</p>"
    + "<p>The review typically takes 5 business days.</p>"
    + "</body></html>"
)


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        openai_api_key="test-key",
        openai_model="fake-model",
        reasoning_effort="",
        web_search_tool="web_search",
        composio_api_key="",
        access_code="",
        rate_per_hour=100,
        max_concurrent_jobs=2,
        data_dir=tmp_path,
        curated_dir=APP_DIR / "data" / "guides",
    )
    values.update(overrides)
    return Settings(**values)


class FakeLLM:
    """Stands in for app.agent.llm.LLM. Returns Responses-API-shaped dicts."""

    model = "fake-model"

    def __init__(self, guide: dict | None = None, fail_research: bool = False) -> None:
        self.guide = guide or fake_guide_json()
        self.fail_research = fail_research
        self.research_calls = 0
        self.structured_calls = 0

    async def research(self, instructions: str, prompt: str) -> dict:
        self.research_calls += 1
        if self.fail_research:
            raise RuntimeError("search unavailable")
        notes = (
            "OFFICIAL_DOMAINS: acme.example, docs.acme.example\n"
            "OAUTH_SUPPORT: yes\n"
            "FINDINGS:\n"
            '- Apps need review | QUOTE: "All apps that request the acme.write scope must pass app review" '
            "| URL: https://docs.acme.example/oauth?utm_source=openai\n"
            "CONFLICTS:\n- none\n"
            "UNKNOWNS:\n- Review cost is not published\n"
        )
        return {
            "output": [
                {"type": "web_search_call", "action": {"type": "search", "query": "acme oauth app review",
                                                       "sources": [{"type": "url", "url": "https://blog.other.example/acme"}]}},
                {"type": "message", "content": [{"type": "output_text", "text": notes, "annotations": [
                    {"type": "url_citation", "url": "https://docs.acme.example/oauth?utm_source=openai", "title": "Acme OAuth docs"}
                ]}]},
            ]
        }

    async def structured(self, instructions: str, prompt: str, schema_name: str, schema: dict) -> dict:
        self.structured_calls += 1
        assert "[1]" in prompt, "sources should be numbered in the prompt"
        return json.loads(json.dumps(self.guide))


def _claim(text, evidence="inferred", ids=None, quote=""):
    return {"text": text, "evidence": evidence, "source_ids": ids or [], "quote": quote}


def _item(title, detail, evidence="inferred", ids=None, quote=""):
    return {"title": title, "detail": detail, "evidence": evidence, "source_ids": ids or [], "quote": quote}


def fake_guide_json() -> dict:
    """A model output that exercises every evidence rule."""
    return {
        "app_name": "Acme",
        "app_url": "https://acme.example",
        "summary": "Hard. Acme requires app review for write scopes.",
        "oauth_support": "yes",
        "oauth_support_claim": _claim("Acme supports OAuth 2.0.", "cited", [1], ""),
        "difficulty": "hard",
        "difficulty_claim": _claim("Write scopes need review.", "cited", [1],
                                   "All apps that request the acme.write scope must pass app review"),
        "composio_status": "unknown",
        "composio_claim": _claim("Unknown.", "unknown"),
        "endpoints": {
            "developer_console_url": "https://console.acme.example/apps",
            "docs_url": "https://docs.acme.example/oauth",
            "authorization_url": "https://auth.acme.example/authorize",
            "token_url": "https://auth.acme.example/token",
        },
        "time": {
            "hands_on": _claim("30 minutes"),
            "approval_wait": _claim("5 business days", "cited", [1], "The review typically takes 5 business days."),
            "fastest_path": _claim("Use a test account", "cited", [1], "This sentence is not on the page at all, invented."),
        },
        "phases": [
            {"name": "Create the app", "summary": "", "steps": [
                {**_item("Register", "Fill the form", "cited", [99]), "who": "human", "why_human": "login", "url": ""},
                {**_item("Pick scopes", "Minimal", "tested", [1]), "who": "agent", "why_human": "", "url": ""},
            ]},
        ],
        "requirements": [_item("Privacy policy", "Needed", "cited", [2], "")],
        "gotchas": [],
        "blockers": [{**_item("Review", "Can be slow"), "workaround": "Apply early"}],
        "agent_can": ["Draft the form"],
        "human_must": ["Log in"],
        "conflicts": [{"topic": "x", "positions": "a vs b", "resolution": "", "source_ids": [1, 42]}],
        "unknowns": ["Cost"],
    }


def mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://auth.acme.example/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "authorization_endpoint": "https://auth.acme.example/authorize",
                "token_endpoint": "https://auth.acme.example/token",
                "scopes_supported": ["openid", "acme.read", "acme.write"],
                "code_challenge_methods_supported": ["S256"],
            })
        if url.startswith("https://auth.acme.example/authorize"):
            return httpx.Response(400, text="missing client_id")
        if url == "https://console.acme.example/apps":
            return httpx.Response(302, headers={"location": "https://login.acme.example/signin?next=/apps"})
        if url.startswith("https://login.acme.example/signin"):
            return httpx.Response(200, text="<html>Sign in</html>", headers={"content-type": "text/html"})
        if url.startswith("https://docs.acme.example/oauth"):
            return httpx.Response(200, text=DOC_PAGE, headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


@pytest.fixture
def no_dns(monkeypatch):
    """Skip DNS checks for fake hosts (the safety check itself is tested separately)."""

    async def allow(url: str) -> None:
        return None

    monkeypatch.setattr(netsafe, "assert_public_url", allow)


@pytest.fixture
def fake_http():
    return httpx.AsyncClient(transport=mock_transport(), follow_redirects=False)
