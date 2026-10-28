"""FastAPI app: guide library, guide pages, live generation with streamed progress."""

from __future__ import annotations

import hmac
import json
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .agent.pipeline import normalize_query, run_pipeline
from .config import APP_DIR, Settings, get_settings
from .jobs import Job, JobManager, RateLimiter
from .models import EVIDENCE_ORDER, TIER_INFO
from .render import guide_to_markdown
from .store import GuideStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class GenerateRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    force: bool = False
    access_code: str = Field(default="", max_length=200)


def create_app(settings: Optional[Settings] = None, store: Optional[GuideStore] = None,
               pipeline=run_pipeline) -> FastAPI:
    settings = settings or get_settings()
    store = store or GuideStore(settings.curated_dir, settings.data_dir / "guides.db")
    jobs = JobManager(settings.max_concurrent_jobs)
    limiter = RateLimiter(settings.rate_per_hour)
    templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
    templates.env.globals.update(TIER_INFO=TIER_INFO, EVIDENCE_ORDER=EVIDENCE_ORDER)

    app = FastAPI(title="OAuth App Setup Guides", docs_url="/api/docs", redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
    app.state.settings = settings
    app.state.store = store
    app.state.jobs = jobs

    def page(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
        ctx.setdefault("settings", settings)
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    # ---------------- pages ----------------

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return page(request, "index.html", curated=store.list_curated(), generated=store.list_generated(12),
                    load_errors=store.load_errors)

    @app.get("/guides/{slug}", response_class=HTMLResponse)
    async def guide_page(request: Request, slug: str):
        guide = store.get(slug)
        if guide is None:
            raise HTTPException(404, "Guide not found")
        return page(request, "guide.html", g=guide, base_url=str(request.base_url).rstrip("/"))

    @app.get("/guides/{slug}/md", response_class=PlainTextResponse)
    async def guide_markdown(request: Request, slug: str):
        guide = store.get(slug)
        if guide is None:
            raise HTTPException(404, "Guide not found")
        return PlainTextResponse(guide_to_markdown(guide, str(request.base_url).rstrip("/")),
                                 media_type="text/markdown; charset=utf-8")

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    async def job_page(request: Request, job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "This generation job has expired. Start a new one from the home page.")
        return page(request, "job.html", job=job)

    @app.get("/audit", response_class=HTMLResponse)
    async def audit_page(request: Request):
        return page(request, "audit.html", curated=store.list_curated())

    @app.get("/about", response_class=HTMLResponse)
    async def about_page(request: Request):
        return page(request, "about.html")

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "curated": len(store.curated), "generation_enabled": settings.can_generate,
                "load_errors": len(store.load_errors)}

    # ---------------- API ----------------

    @app.get("/api/guides")
    async def api_list():
        return {
            "curated": [{"slug": g.slug, "app_name": g.app_name, "difficulty": g.difficulty} for g in store.list_curated()],
            "generated": store.list_generated(50),
        }

    @app.get("/api/guides/{slug}")
    async def api_guide(slug: str):
        guide = store.get(slug)
        if guide is None:
            raise HTTPException(404, "Guide not found")
        return JSONResponse(json.loads(guide.model_dump_json()))

    @app.post("/api/generate")
    async def api_generate(req: GenerateRequest, request: Request):
        try:
            normalize_query(req.query)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        if not req.force:
            curated = store.find_curated(req.query)
            if curated:
                return {"redirect": f"/guides/{curated.slug}", "cached": "curated"}
            existing = store.latest_generated(req.query)
            if existing:
                return {"redirect": f"/guides/{existing.slug}", "cached": "generated",
                        "generated_at": existing.generated_at}
        if not settings.can_generate:
            raise HTTPException(503, "Live generation is disabled: OPENAI_API_KEY is not set on the server.")
        if settings.access_code and not hmac.compare_digest(req.access_code.strip(), settings.access_code):
            raise HTTPException(403, "Live generation needs the access code for this deployment.")
        if not limiter.allow(_client_ip(request)):
            raise HTTPException(429, f"Limit of {settings.rate_per_hour} live generations per hour reached.")
        if jobs.running_count() >= settings.max_concurrent_jobs * 3:
            raise HTTPException(503, "Too many generations in progress. Try again shortly.")

        query = req.query.strip()

        async def runner(job: Job) -> str:
            guide = await pipeline(query, settings, job.emit)
            store.save(guide, query)
            return guide.slug

        job = jobs.start(query, runner)
        return {"job_id": job.id, "url": f"/jobs/{job.id}"}

    @app.get("/api/jobs/{job_id}")
    async def api_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return {"id": job.id, "query": job.query, "status": job.status, "slug": job.slug, "error": job.error,
                "events": job.events}

    @app.get("/api/jobs/{job_id}/events")
    async def api_job_events(job_id: str, request: Request):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        try:
            start = int(request.headers.get("last-event-id", "-1")) + 1
        except ValueError:
            start = 0

        async def stream():
            i = max(0, start)
            yield "retry: 3000\n\n"
            while True:
                while i < len(job.events):
                    yield f"id: {i}\ndata: {json.dumps(job.events[i])}\n\n"
                    i += 1
                if job.finished and i >= len(job.events):
                    break
                if await request.is_disconnected():
                    break
                await job.wait_for_events(i, timeout=15)
                if i >= len(job.events) and not job.finished:
                    yield ": keep-alive\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith(("/api/", "/static/")):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return page(request, "error.html", status_code=exc.status_code, status=exc.status_code, detail=exc.detail)

    return app


def _client_ip(request: Request) -> str:
    # uvicorn runs with proxy_headers, so client.host is already the forwarded address.
    # Headers can be spoofed; ACCESS_CODE is the real protection for a public deployment.
    return request.client.host if request.client else "unknown"


app = create_app()
