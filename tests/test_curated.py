"""Curated guides must load and be internally consistent (every reference resolves)."""

from __future__ import annotations

import json

import pytest

from app.config import APP_DIR
from app.models import Guide

GUIDE_FILES = sorted((APP_DIR / "data" / "guides").glob("*.json"))


def test_there_are_curated_guides_covering_several_tiers():
    assert len(GUIDE_FILES) >= 4
    tiers = {Guide.model_validate_json(p.read_text(encoding="utf-8")).difficulty for p in GUIDE_FILES}
    assert {"trivial", "moderate", "hard", "gated"} <= tiers


@pytest.mark.parametrize("path", GUIDE_FILES, ids=lambda p: p.stem)
def test_guide_file_is_valid_and_consistent(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    guide = Guide.model_validate(raw)
    assert guide.slug == path.stem
    assert guide.summary and guide.app_name
    ids = [s.id for s in guide.sources]
    assert len(ids) == len(set(ids)), "duplicate source ids"
    for item in guide.iter_evidenced():
        for sid in item.source_ids:
            assert sid in ids, f"{path.name}: source [{sid}] referenced but not listed"
        if item.evidence == "cited":
            assert item.source_ids, f"{path.name}: cited claim without a source: {item}"
        if item.quote:
            assert len(item.quote.split()) <= 35, f"{path.name}: quote too long: {item.quote}"
        if item.quote_check == "verified":
            assert item.quote, f"{path.name}: verified quote_check without a quote"
    for c in guide.conflicts:
        assert all(sid in ids for sid in c.source_ids)
    for a in guide.audit:
        assert all(sid in ids for sid in a.source_ids)
    for s in guide.sources:
        assert s.url.startswith("https://")
    assert guide.phases and all(p.steps for p in guide.phases)
    assert guide.agent_can and guide.human_must
    assert guide.audit, "curated guides should record what was spot-checked"
