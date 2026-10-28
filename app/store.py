"""Guide storage: curated guides from JSON files in the repo, generated guides in SQLite."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .models import Guide, slugify

log = logging.getLogger(__name__)


class GuideStore:
    def __init__(self, curated_dir: Path, db_path: Path) -> None:
        self.curated: dict[str, Guide] = {}
        self.load_errors: list[str] = []
        for path in sorted(curated_dir.glob("*.json")):
            try:
                guide = Guide.model_validate_json(path.read_text(encoding="utf-8"))
                guide.origin = "curated"
                self.curated[guide.slug] = guide
            except Exception as exc:  # a broken file should not take the site down
                msg = f"{path.name}: {exc}"
                self.load_errors.append(msg)
                log.error("Could not load curated guide %s", msg)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._lock = threading.Lock()
        with self._lock, self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS guides ("
                " slug TEXT PRIMARY KEY, app_name TEXT NOT NULL, difficulty TEXT NOT NULL,"
                " created_at TEXT NOT NULL, json TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS app_keys ("
                " app_key TEXT NOT NULL, slug TEXT NOT NULL, created_at TEXT NOT NULL,"
                " PRIMARY KEY (app_key, slug))"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- lookups ----

    def get(self, slug: str) -> Optional[Guide]:
        if slug in self.curated:
            return self.curated[slug]
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT json FROM guides WHERE slug = ?", (slug,)).fetchone()
        return Guide.model_validate_json(row[0]) if row else None

    def find_curated(self, query: str) -> Optional[Guide]:
        key = app_key(query)
        for guide in self.curated.values():
            names = {app_key(guide.slug), app_key(guide.app_name), *(app_key(a) for a in guide.aliases)}
            if key in names:
                return guide
        return None

    def latest_generated(self, query: str) -> Optional[Guide]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT g.json FROM app_keys k JOIN guides g ON g.slug = k.slug"
                " WHERE k.app_key = ? ORDER BY k.created_at DESC LIMIT 1",
                (app_key(query),),
            ).fetchone()
        return Guide.model_validate_json(row[0]) if row else None

    def list_curated(self) -> list[Guide]:
        order = {"trivial": 0, "moderate": 1, "hard": 2, "gated": 3, "no_oauth": 4, "unknown": 5}
        return sorted(self.curated.values(), key=lambda g: (order.get(g.difficulty, 9), g.app_name.lower()))

    def list_generated(self, limit: int = 30) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT slug, app_name, difficulty, created_at FROM guides ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{"slug": r[0], "app_name": r[1], "difficulty": r[2], "created_at": r[3]} for r in rows]

    # ---- writes ----

    def save(self, guide: Guide, query: str) -> None:
        keys = {app_key(query), app_key(guide.app_name)} - {"app"}
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO guides (slug, app_name, difficulty, created_at, json) VALUES (?,?,?,?,?)",
                (guide.slug, guide.app_name, guide.difficulty, guide.generated_at, guide.model_dump_json()),
            )
            for key in keys:
                conn.execute(
                    "INSERT OR REPLACE INTO app_keys (app_key, slug, created_at) VALUES (?,?,?)",
                    (key, guide.slug, guide.generated_at),
                )


def app_key(value: str) -> str:
    """Normalize 'https://www.notion.so/product', 'notion.so' and 'Notion' to comparable keys."""
    value = (value or "").lower().strip()
    for prefix in ("https://", "http://"):
        value = value.removeprefix(prefix)
    if "." in value and " " not in value:
        host = value.removeprefix("www.").split("/")[0]
        labels = host.split(".")
        value = labels[-2] if len(labels) >= 2 else labels[0]
        if value in ("co", "com") and len(labels) >= 3:
            value = labels[-3]
    return slugify(value)
