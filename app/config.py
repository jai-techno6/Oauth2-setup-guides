"""Runtime configuration, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent


def load_dotenv(path: Path) -> None:
    """Minimal .env loader. Existing environment variables always win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_model: str
    reasoning_effort: str
    web_search_tool: str
    composio_api_key: str
    access_code: str
    rate_per_hour: int
    max_concurrent_jobs: int
    data_dir: Path
    curated_dir: Path

    @property
    def can_generate(self) -> bool:
        return bool(self.openai_api_key)


def get_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    data_dir = Path(os.environ.get("DATA_DIR", "").strip() or (ROOT / "var"))
    return Settings(
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        openai_model=os.environ.get("OPENAI_MODEL", "").strip() or "gpt-5.4-mini",
        reasoning_effort=os.environ.get("OPENAI_REASONING_EFFORT", "low").strip().lower(),
        web_search_tool=os.environ.get("OPENAI_WEB_SEARCH_TOOL", "").strip() or "web_search",
        composio_api_key=os.environ.get("COMPOSIO_API_KEY", "").strip(),
        access_code=os.environ.get("ACCESS_CODE", "").strip(),
        rate_per_hour=_int("GENERATE_RATE_PER_HOUR", 10),
        max_concurrent_jobs=max(1, _int("MAX_CONCURRENT_JOBS", 3)),
        data_dir=data_dir,
        curated_dir=APP_DIR / "data" / "guides",
    )
