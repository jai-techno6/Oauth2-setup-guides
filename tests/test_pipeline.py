"""End-to-end pipeline run with a fake LLM and a mocked HTTP layer."""

from __future__ import annotations

import asyncio

import pytest

from app.agent.llm import output_text, web_sources
from app.agent.pipeline import PipelineError, normalize_query, run_pipeline
from app.agent.research import clean_url, parse_notes
from app.agent.synthesize import GUIDE_SCHEMA

from .conftest import FakeLLM, make_settings


def _walk(schema, path="$"):
    if schema.get("type") == "object":
        props = schema["properties"]
        assert schema.get("additionalProperties") is False, f"{path}: additionalProperties must be false"
        assert sorted(schema["required"]) == sorted(props), f"{path}: all properties must be required (strict mode)"
        for k, v in props.items():
            _walk(v, f"{path}.{k}")
    elif schema.get("type") == "array":
        _walk(schema["items"], f"{path}[]")


def test_guide_schema_is_valid_for_openai_strict_mode():
    _walk(GUIDE_SCHEMA)


def test_fake_output_matches_schema_keys():
    from .conftest import fake_guide_json
    assert set(fake_guide_json()) == set(GUIDE_SCHEMA["properties"])


@pytest.mark.parametrize("raw,name,url", [
    ("Notion", "Notion", ""),
    ("notion.so", "Notion", "https://notion.so"),
    ("https://www.zoom.us/pricing", "Zoom", "https://zoom.us"),
    ("bbc.co.uk", "Bbc", "https://bbc.co.uk"),
    ("Google Drive", "Google Drive", ""),
])
def test_normalize_query(raw, name, url):
    assert normalize_query(raw) == (name, url)


@pytest.mark.parametrize("raw", ["", "a", "x" * 200])
def test_normalize_query_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        normalize_query(raw)


def test_research_parsing_helpers():
    assert clean_url("https://a.example/x?utm_source=openai&id=3#frag") == "https://a.example/x?id=3"
    domains, support, urls = parse_notes(
        "OFFICIAL_DOMAINS: www.acme.example, *.docs.acme.example\nOAUTH_SUPPORT: Partial (only for partners)\n"
        "- f | QUOTE: \"q\" | URL: https://acme.example/a."
    )
    assert domains == ["acme.example", "docs.acme.example"]
    assert support == "partial"
    assert urls == ["https://acme.example/a"]
    data = {"output": [
        {"type": "web_search_call", "action": {"query": "q1", "sources": [{"url": "https://s.example"}]}},
        {"type": "message", "content": [{"type": "output_text", "text": "hi",
                                          "annotations": [{"type": "url_citation", "url": "https://c.example", "title": "C"}]}]},
    ]}
    cited, consulted, queries = web_sources(data)
    assert cited == [("https://c.example", "C")] and consulted == [("https://s.example", "")] and queries == ["q1"]
    assert output_text(data) == "hi"


def test_pipeline_end_to_end(tmp_path, no_dns, fake_http):
    events = []

    async def emit(type_, **data):
        events.append((type_, data))

    llm = FakeLLM()
    guide = asyncio.run(run_pipeline("Acme", make_settings(tmp_path), emit, llm=llm, http=fake_http))

    assert llm.research_calls == 3 and llm.structured_calls == 1
    assert guide.origin == "agent" and guide.difficulty == "hard"
    assert guide.slug.startswith("acme-")

    # sources: tracking params removed, kinds classified from OFFICIAL_DOMAINS
    by_url = {s.url: s for s in guide.sources}
    assert by_url["https://docs.acme.example/oauth"].kind == "official"
    assert by_url["https://blog.other.example/acme"].kind == "third_party"

    # evidence rules
    assert guide.difficulty_claim.quote_check == "verified" and guide.difficulty_claim.evidence == "cited"
    assert guide.time.approval_wait.quote_check == "verified"
    assert guide.time.fastest_path.evidence == "inferred"            # invented quote -> downgraded
    assert guide.time.fastest_path.quote_check == "not_found"
    register, scopes = guide.phases[0].steps
    assert register.evidence == "inferred" and register.source_ids == []  # bad source id 99
    assert scopes.evidence == "inferred"                                   # model may not claim "tested"
    assert guide.conflicts[0].source_ids == [1]

    # probes: discovery, live authorize endpoint, console login wall
    outcomes = {p.check: p.outcome for p in guide.probes}
    assert outcomes["OAuth/OpenID discovery"] == "pass"
    assert outcomes["Authorization endpoint reachable"] == "pass"
    assert outcomes["Developer console (where the agent stops)"] == "blocked"
    assert guide.endpoints.evidence == "tested"
    assert guide.walls() and "login.acme.example" in guide.walls()[0].result

    # research UNKNOWNS merged, progress streamed for every stage
    assert guide.unknowns == ["Cost"]  # the guide's own list wins; research notes only fill an empty list
    stages = {d.get("stage") for t, d in events if t == "step"}
    assert {"resolve", "research", "sources", "synthesize", "probe", "verify", "done"} <= stages
    assert guide.pipeline_log


def test_pipeline_fails_cleanly_when_all_research_fails(tmp_path, no_dns, fake_http):
    async def emit(type_, **data):
        pass

    with pytest.raises(PipelineError):
        asyncio.run(run_pipeline("Acme", make_settings(tmp_path), emit, llm=FakeLLM(fail_research=True), http=fake_http))

def test_placeholder_urls_are_not_sources():
    from app.agent.research import is_real_url
    for bad in ["https://`hostname`/services/oauth2/authorize`", "http://localhost:1717/OauthRedirect`",
                "https://localhost`", "https://yourdomain.com/callback", "https://{instance}.salesforce.com/x"]:
        assert not is_real_url(bad), bad
    for good in ["https://help.salesforce.com/s/articleView?id=x", "https://developers.zoom.us/docs/integrations/`"]:
        assert is_real_url(good), good