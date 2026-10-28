"""The guide schema. Curated guides (JSON files) and agent-generated guides use the same model.

Every claim a developer might act on carries an evidence label:
  tested   - observed directly by a check we ran (HTTP probe, API call), with the date.
  cited    - stated by a linked source; for agent guides the quote is checked against the page.
  inferred - reasoning from cited facts or general knowledge; no source states it.
  unknown  - we could not determine it.
"""

from __future__ import annotations

import re
from typing import Iterator, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Evidence = Literal["tested", "cited", "inferred", "unknown"]
Tier = Literal["trivial", "moderate", "hard", "gated", "no_oauth", "unknown"]
Who = Literal["agent", "human", "either"]
QuoteCheck = Literal["verified", "not_found", "not_checked", "no_quote"]
SourceKind = Literal["official", "third_party", "composio", "probe"]

EVIDENCE_ORDER: tuple[str, ...] = ("tested", "cited", "inferred", "unknown")

TIER_INFO: dict[str, dict[str, str]] = {
    "trivial": {
        "label": "Trivial",
        "blurb": "Self-serve form, credentials in minutes, no review before real users can connect.",
    },
    "moderate": {
        "label": "Moderate",
        "blurb": "Self-serve setup, but distribution limits or a listing review apply before you scale.",
    },
    "hard": {
        "label": "Hard",
        "blurb": "Provider review, verification, or security assessment before broad use. Plan in weeks.",
    },
    "gated": {
        "label": "Gated",
        "blurb": "Useful access needs partner approval, a legal business entity, or a paid API tier.",
    },
    "no_oauth": {
        "label": "No OAuth",
        "blurb": "The provider does not offer third-party OAuth. Another auth method is needed.",
    },
    "unknown": {
        "label": "Unknown",
        "blurb": "The agent could not find enough evidence to place this app.",
    },
}


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Source(_Base):
    id: int
    url: str
    title: str = ""
    kind: SourceKind = "third_party"
    accessed: str = ""


class Evidenced(_Base):
    evidence: Evidence = "unknown"
    source_ids: list[int] = Field(default_factory=list)
    quote: str = ""
    quote_check: QuoteCheck = "no_quote"
    note: str = ""


class Claim(Evidenced):
    text: str = ""


class Item(Evidenced):
    title: str
    detail: str = ""


class Step(Item):
    who: Who = "human"
    why_human: str = ""
    url: str = ""


class Blocker(Item):
    workaround: str = ""


class Phase(_Base):
    name: str
    summary: str = ""
    steps: list[Step] = Field(default_factory=list)


class TimeEstimate(_Base):
    hands_on: Claim = Field(default_factory=Claim)
    approval_wait: Claim = Field(default_factory=Claim)
    fastest_path: Claim = Field(default_factory=Claim)


class Endpoints(_Base):
    developer_console_url: str = ""
    docs_url: str = ""
    authorization_url: str = ""
    token_url: str = ""
    evidence: Evidence = "unknown"
    note: str = ""


class Composio(_Base):
    status: Literal["managed", "byo_required", "unknown"] = "unknown"
    claim: Claim = Field(default_factory=Claim)


class Conflict(_Base):
    topic: str
    positions: str
    resolution: str = ""
    source_ids: list[int] = Field(default_factory=list)


class Probe(_Base):
    check: str
    url: str = ""
    outcome: Literal["pass", "fail", "blocked", "error", "info"] = "info"
    result: str = ""
    checked_at: str = ""
    method: str = ""


class AuditEntry(_Base):
    claim: str
    status: Literal["verified", "corrected", "unverified", "conflict"]
    method: str = ""
    note: str = ""
    source_ids: list[int] = Field(default_factory=list)


class Guide(_Base):
    slug: str
    app_name: str
    app_url: str = ""
    aliases: list[str] = Field(default_factory=list)
    origin: Literal["curated", "agent"] = "agent"
    generated_at: str = ""
    model: str = ""
    query: str = ""

    summary: str = ""
    oauth_support: Literal["yes", "no", "partial", "unknown"] = "unknown"
    oauth_support_claim: Claim = Field(default_factory=Claim)
    difficulty: Tier = "unknown"
    difficulty_claim: Claim = Field(default_factory=Claim)
    composio: Composio = Field(default_factory=Composio)
    endpoints: Endpoints = Field(default_factory=Endpoints)
    time: TimeEstimate = Field(default_factory=TimeEstimate)

    phases: list[Phase] = Field(default_factory=list)
    requirements: list[Item] = Field(default_factory=list)
    gotchas: list[Item] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)
    agent_can: list[str] = Field(default_factory=list)
    human_must: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)

    sources: list[Source] = Field(default_factory=list)
    probes: list[Probe] = Field(default_factory=list)
    audit: list[AuditEntry] = Field(default_factory=list)
    pipeline_log: list[str] = Field(default_factory=list)

    # ---- helpers used by templates, verification and the markdown export ----

    def iter_evidenced(self) -> Iterator[Evidenced]:
        yield self.oauth_support_claim
        yield self.difficulty_claim
        yield self.composio.claim
        yield self.time.hands_on
        yield self.time.approval_wait
        yield self.time.fastest_path
        for phase in self.phases:
            yield from phase.steps
        yield from self.requirements
        yield from self.gotchas
        yield from self.blockers

    def evidence_counts(self) -> dict[str, int]:
        counts = {label: 0 for label in EVIDENCE_ORDER}
        for item in self.iter_evidenced():
            counts[item.evidence] = counts.get(item.evidence, 0) + 1
        return counts

    def confidence(self) -> str:
        """Overall confidence from the share of claims backed by a test or a source."""
        counts = self.evidence_counts()
        total = sum(counts.values())
        if total == 0:
            return "low"
        backed = (counts["tested"] + counts["cited"]) / total
        if backed >= 0.7 and counts["unknown"] <= 2:
            return "high"
        if backed >= 0.4:
            return "medium"
        return "low"

    def source(self, source_id: int) -> Optional[Source]:
        for src in self.sources:
            if src.id == source_id:
                return src
        return None

    def tier_label(self) -> str:
        return TIER_INFO.get(self.difficulty, TIER_INFO["unknown"])["label"]

    def tier_blurb(self) -> str:
        return TIER_INFO.get(self.difficulty, TIER_INFO["unknown"])["blurb"]

    def walls(self) -> list[Probe]:
        """Probes where an automated agent was stopped (login, bot protection)."""
        return [p for p in self.probes if p.outcome == "blocked"]


_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(value: str, max_len: int = 48) -> str:
    slug = _slug_re.sub("-", value.lower()).strip("-")
    return slug[:max_len].strip("-") or "app"
