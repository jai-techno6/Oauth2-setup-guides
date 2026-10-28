"""Thin wrapper over the OpenAI Responses API.

Kept small and defensive: responses are converted to plain dicts so parsing does not depend
on SDK object shapes, and an unsupported `reasoning` parameter is retried without it.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from openai import AsyncOpenAI, BadRequestError

log = logging.getLogger(__name__)


class LLM:
    def __init__(self, api_key: str, model: str, reasoning_effort: str = "", web_search_tool: str = "web_search",
                 client: Optional[Any] = None) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort if reasoning_effort in ("low", "medium", "high") else ""
        self.web_search_tool = web_search_tool
        self.client = client or AsyncOpenAI(api_key=api_key, timeout=240.0, max_retries=2)

    async def _create(self, **kwargs: Any) -> dict:
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        try:
            resp = await self.client.responses.create(model=self.model, **kwargs)
        except BadRequestError as exc:
            if "reasoning" in kwargs and "reasoning" in str(exc).lower():
                log.warning("Model rejected reasoning parameter; retrying without it")
                kwargs.pop("reasoning")
                self.reasoning_effort = ""
                resp = await self.client.responses.create(model=self.model, **kwargs)
            else:
                raise
        return to_dict(resp)

    async def research(self, instructions: str, prompt: str) -> dict:
        return await self._create(
            instructions=instructions,
            input=prompt,
            tools=[{"type": self.web_search_tool}],
            include=["web_search_call.action.sources"],
        )

    async def structured(self, instructions: str, prompt: str, schema_name: str, schema: dict) -> dict:
        data = await self._create(
            instructions=instructions,
            input=prompt,
            text={"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
        )
        return parse_json(output_text(data))


def to_dict(resp: Any) -> dict:
    if isinstance(resp, dict):
        return resp
    if hasattr(resp, "model_dump"):
        return resp.model_dump()
    return json.loads(json.dumps(resp, default=lambda o: getattr(o, "__dict__", str(o))))


def output_text(data: dict) -> str:
    """Concatenate all output_text parts (same as the SDK's `response.output_text`)."""
    if isinstance(data.get("output_text"), str) and data["output_text"]:
        return data["output_text"]
    parts: list[str] = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts)


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def web_sources(data: dict) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    """Return (cited, consulted, queries) from a web-search response.

    cited     - url_citation annotations in the answer text (the model relied on these)
    consulted - every source the search tool returned
    queries   - the search queries the model ran
    """
    cited: list[tuple[str, str]] = []
    consulted: list[tuple[str, str]] = []
    queries: list[str] = []
    for item in data.get("output") or []:
        kind = item.get("type")
        if kind == "web_search_call":
            action = item.get("action") or {}
            if action.get("query"):
                queries.append(str(action["query"]))
            for q in action.get("queries") or []:
                if q:
                    queries.append(str(q))
            for src in action.get("sources") or []:
                if isinstance(src, dict) and src.get("url"):
                    consulted.append((src["url"], src.get("title") or ""))
        elif kind == "message":
            for content in item.get("content") or []:
                for ann in content.get("annotations") or []:
                    if ann.get("type") == "url_citation" and ann.get("url"):
                        cited.append((ann["url"], ann.get("title") or ""))
    return cited, consulted, queries
