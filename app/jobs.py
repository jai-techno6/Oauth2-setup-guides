"""In-memory job runner for live generation, with replayable progress events for SSE."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger(__name__)

JOB_TTL_SECONDS = 3600


@dataclass
class Job:
    id: str
    query: str
    created: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)
    status: str = "queued"  # queued | running | done | error
    slug: str = ""
    error: str = ""
    finished: bool = False
    _cond: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def emit(self, type_: str, **data: Any) -> None:
        event = {"type": type_, "t": round(time.time() - self.created, 1), **data}
        async with self._cond:
            self.events.append(event)
            self._cond.notify_all()

    async def finish(self) -> None:
        async with self._cond:
            self.finished = True
            self._cond.notify_all()

    async def wait_for_events(self, seen: int, timeout: float) -> None:
        async with self._cond:
            if len(self.events) > seen or self.finished:
                return
            try:
                await asyncio.wait_for(self._cond.wait(), timeout)
            except asyncio.TimeoutError:
                pass


Runner = Callable[[Job], Awaitable[str]]  # returns the saved guide slug


class JobManager:
    def __init__(self, max_concurrent: int) -> None:
        self.jobs: dict[str, Job] = {}
        self._sem = asyncio.Semaphore(max_concurrent)
        self._tasks: set[asyncio.Task] = set()

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def running_count(self) -> int:
        return sum(1 for j in self.jobs.values() if not j.finished)

    def start(self, query: str, runner: Runner) -> Job:
        self._gc()
        job = Job(id=secrets.token_urlsafe(9), query=query)
        self.jobs[job.id] = job
        task = asyncio.create_task(self._run(job, runner))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(self, job: Job, runner: Runner) -> None:
        if self._sem.locked():
            await job.emit("step", stage="queue", message="Waiting for a free agent slot")
        async with self._sem:
            job.status = "running"
            try:
                slug = await runner(job)
                job.slug = slug
                job.status = "done"
                await job.emit("done", slug=slug, url=f"/guides/{slug}")
            except Exception as exc:
                log.exception("Job %s failed", job.id)
                job.status = "error"
                job.error = _friendly(exc)
                await job.emit("error", message=job.error)
            finally:
                await job.finish()

    def _gc(self) -> None:
        cutoff = time.time() - JOB_TTL_SECONDS
        for jid in [j.id for j in self.jobs.values() if j.finished and j.created < cutoff]:
            self.jobs.pop(jid, None)


def _friendly(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc)
    if "AuthenticationError" in name or "invalid_api_key" in text:
        return "The OpenAI API key was rejected. Check OPENAI_API_KEY."
    if "RateLimitError" in name or "insufficient_quota" in text:
        return "OpenAI rate limit or quota reached. Try again in a minute, or check billing."
    if "NotFoundError" in name and "model" in text.lower():
        return "The configured OPENAI_MODEL was not found for this API key."
    return f"{name}: {text}"[:400]


class RateLimiter:
    def __init__(self, per_hour: int) -> None:
        self.per_hour = per_hour
        self.hits: dict[str, deque] = {}

    def allow(self, key: str) -> bool:
        if self.per_hour <= 0:
            return True
        now = time.time()
        q = self.hits.setdefault(key, deque())
        while q and q[0] < now - 3600:
            q.popleft()
        if len(q) >= self.per_hour:
            return False
        q.append(now)
        return True
