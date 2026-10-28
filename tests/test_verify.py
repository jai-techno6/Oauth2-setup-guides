from __future__ import annotations

import asyncio

from app.agent.netsafe import FetchResult
from app.agent.verify import check_quotes, enforce_rules, normalize, quote_in_page
from app.models import Claim, Guide, Item, Source, TimeEstimate

PAGE = "<p>" + "Some filler words here. " * 100 + "Brand verification typically takes 2-3 business days.</p>"


def test_quote_matching_tolerates_punctuation_and_case():
    page = normalize(PAGE)
    assert quote_in_page("brand verification typically takes 2–3 business days", page)
    assert quote_in_page("“Brand verification typically takes 2-3 business days.”", page)
    assert not quote_in_page("Brand verification takes 6 months", page)
    assert not quote_in_page("", page)


def _guide(**kw) -> Guide:
    return Guide(slug="t", app_name="T", origin="agent", sources=[Source(id=1, url="https://a.example/doc")], **kw)


def test_enforce_rules_downgrades_unsupported_claims():
    g = _guide(
        difficulty_claim=Claim(text="x", evidence="cited", source_ids=[7]),
        oauth_support_claim=Claim(text="y", evidence="tested", source_ids=[1]),
        requirements=[Item(title="r", evidence="cited", source_ids=[1], quote="q")],
    )
    enforce_rules(g)
    assert g.difficulty_claim.evidence == "inferred" and g.difficulty_claim.source_ids == []
    assert g.oauth_support_claim.evidence == "inferred"
    assert g.requirements[0].evidence == "cited" and g.requirements[0].quote_check == "not_checked"


def test_check_quotes_verifies_downgrades_and_skips_unreadable():
    g = _guide(time=TimeEstimate(
        hands_on=Claim(text="a", evidence="cited", source_ids=[1], quote="Brand verification typically takes 2-3 business days"),
        approval_wait=Claim(text="b", evidence="cited", source_ids=[1], quote="Approval is instant for everyone"),
    ))
    g.sources.append(Source(id=2, url="https://blocked.example/"))
    g.time.fastest_path = Claim(text="c", evidence="cited", source_ids=[2], quote="anything")

    async def fetch(url: str) -> FetchResult:
        if "blocked" in url:
            return FetchResult(url=url, status=403, body="Forbidden")
        return FetchResult(url=url, status=200, content_type="text/html", body=PAGE)

    counts = asyncio.run(check_quotes(g, fetch))
    assert g.time.hands_on.quote_check == "verified" and g.time.hands_on.evidence == "cited"
    assert g.time.approval_wait.quote_check == "not_found" and g.time.approval_wait.evidence == "inferred"
    assert g.time.fastest_path.quote_check == "not_checked" and g.time.fastest_path.evidence == "cited"
    assert counts == {"verified": 1, "not_found": 1, "not_checked": 1}


def test_confidence_reflects_evidence_mix():
    g = _guide()
    assert g.confidence() == "low"  # all defaults are unknown
    for item in g.iter_evidenced():
        item.evidence = "cited"
    assert g.confidence() == "high"
