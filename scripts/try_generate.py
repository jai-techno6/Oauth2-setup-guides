"""Run the live agent once from the command line, without the web server.

Usage:  python scripts/try_generate.py "Notion"

Prints every progress step, then the guide as Markdown, and saves it to the same
database the web app uses (so it appears under "Recently generated").
Use this to check your OPENAI_API_KEY and model before deploying.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.pipeline import run_pipeline  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.render import guide_to_markdown  # noqa: E402
from app.store import GuideStore  # noqa: E402


async def main(query: str) -> int:
    settings = get_settings()
    if not settings.can_generate:
        print("OPENAI_API_KEY is not set (check your .env file).")
        return 1

    async def emit(type_: str, **data) -> None:
        print(f"[{data.get('stage', type_)}] {data.get('message', '')}", flush=True)

    guide = await run_pipeline(query, settings, emit)
    GuideStore(settings.curated_dir, settings.data_dir / "guides.db").save(guide, query)
    print("\n" + "=" * 80 + "\n")
    print(guide_to_markdown(guide))
    print(f"Saved. Open http://127.0.0.1:8000/guides/{guide.slug} after starting the app.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python scripts/try_generate.py "App name or website"')
        sys.exit(2)
    sys.exit(asyncio.run(main(" ".join(sys.argv[1:]))))
