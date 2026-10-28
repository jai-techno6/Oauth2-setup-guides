"""HTTP-level tests: pages render, API works, live generation streams and saves a guide."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Guide, Source
from app.store import GuideStore, app_key

from .conftest import make_settings


async def fake_pipeline(query, settings, emit):
    await emit("step", stage="research", message=f"researching {query}")
    await emit("step", stage="done", message="ok")
    return Guide(slug="notion-abc123", app_name="Notion", origin="agent", generated_at="2026-10-28 10:00 UTC",
                 summary="Moderate.", difficulty="moderate", query=query,
                 sources=[Source(id=1, url="https://developers.notion.com/")])


def _client(tmp_path, **overrides):
    settings = make_settings(tmp_path, **overrides)
    store = GuideStore(settings.curated_dir, tmp_path / "guides.db")
    return TestClient(create_app(settings=settings, store=store, pipeline=fake_pipeline))


def test_pages_render(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/")
        assert r.status_code == 200 and "Curated guides" in r.text and "GitHub" in r.text
        for slug in ("github", "google-gmail", "slack", "hubspot", "linkedin", "x-twitter"):
            r = c.get(f"/guides/{slug}")
            assert r.status_code == 200, slug
            assert "Step-by-step checklist" in r.text and "Sources" in r.text
            md = c.get(f"/guides/{slug}/md")
            assert md.status_code == 200 and md.text.startswith("# OAuth app setup guide")
            assert c.get(f"/api/guides/{slug}").json()["slug"] == slug
        assert c.get("/audit").status_code == 200
        assert c.get("/about").status_code == 200
        health = c.get("/healthz").json()
        assert health["ok"] and health["curated"] >= 6 and health["load_errors"] == 0


def test_not_found_pages(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/guides/does-not-exist")
        assert r.status_code == 404 and "Back to the guide library" in r.text
        assert c.get("/api/guides/nope").status_code == 404
        assert c.get("/jobs/nope").status_code == 404


def test_generate_redirects_to_curated(tmp_path):
    with _client(tmp_path) as c:
        for q in ("GitHub", "github.com", "https://github.com/", "gmail", "Twitter"):
            body = c.post("/api/generate", json={"query": q}).json()
            assert body.get("cached") == "curated", q


def test_generate_validates_input(tmp_path):
    with _client(tmp_path) as c:
        assert c.post("/api/generate", json={"query": "a"}).status_code == 422
        assert c.post("/api/generate", json={"query": "x" * 300}).status_code == 422


def test_live_generation_streams_and_saves(tmp_path):
    with _client(tmp_path) as c:
        body = c.post("/api/generate", json={"query": "Notion"}).json()
        job_id = body["job_id"]
        assert body["url"] == f"/jobs/{job_id}"
        assert c.get(f"/jobs/{job_id}").status_code == 200

        events = []
        with c.stream("GET", f"/api/jobs/{job_id}/events") as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            for line in r.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        types = [e["type"] for e in events]
        assert types[-1] == "done", events
        assert events[-1]["url"] == "/guides/notion-abc123"

        assert c.get("/guides/notion-abc123").status_code == 200
        assert c.get("/api/jobs/" + job_id).json()["status"] == "done"

        # Same app again (by name or by domain) returns the saved guide instead of re-running.
        again = c.post("/api/generate", json={"query": "notion"}).json()
        assert again["cached"] == "generated" and again["redirect"] == "/guides/notion-abc123"
        # force=true starts a new job
        assert "job_id" in c.post("/api/generate", json={"query": "Notion", "force": True}).json()


def test_access_code_and_disabled_generation(tmp_path):
    with _client(tmp_path, access_code="s3cret") as c:
        assert c.post("/api/generate", json={"query": "Pipedrive"}).status_code == 403
        assert "job_id" in c.post("/api/generate", json={"query": "Pipedrive", "access_code": "s3cret"}).json()
    with _client(tmp_path, openai_api_key="") as c:
        assert c.post("/api/generate", json={"query": "Pipedrive2"}).status_code == 503
        # curated lookups still work without a key
        assert c.post("/api/generate", json={"query": "slack"}).json()["cached"] == "curated"


def test_rate_limit(tmp_path):
    with _client(tmp_path, rate_per_hour=1) as c:
        assert "job_id" in c.post("/api/generate", json={"query": "AppOne"}).json()
        assert c.post("/api/generate", json={"query": "AppTwo"}).status_code == 429


def test_app_key_normalization():
    assert app_key("https://www.notion.so/product") == app_key("notion.so") == app_key("Notion") == "notion"
    assert app_key("bbc.co.uk") == "bbc"
