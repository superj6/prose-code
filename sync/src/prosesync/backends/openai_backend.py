"""OpenAI Responses API backend with strict JSON-schema output, streamed.

Also serves any OpenAI-compatible server (vLLM, llama.cpp) via ``sync.base_url`` /
``OPENAI_BASE_URL`` - that is how the fine-tuned model is plugged in later.
"""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

from omegaconf import DictConfig
from openai import AsyncOpenAI

from ..models import BackendResult
from ..streamjson import ArrayElementScanner


class OpenAIBackend:
    name = "openai"

    def __init__(self, cfg: DictConfig):
        self.cfg = cfg
        base_url = cfg.sync.get("base_url") or os.environ.get("OPENAI_BASE_URL") or None
        self.client = AsyncOpenAI(base_url=base_url, timeout=float(cfg.sync.get("timeout_s", 60)))
        self.model = cfg.sync.model
        self.temperature = cfg.sync.get("temperature")

    async def check_model(self) -> dict[str, Any]:
        """Verify the configured model id exists on the endpoint (run at startup, not per request)."""
        model = await self.client.models.retrieve(self.model)
        return model.model_dump()

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        schema_name: str,
        on_object: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        model: str | None = None,
    ) -> BackendResult:
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "input": messages,
            "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
            "stream": True,
        }
        if self.temperature is not None:
            kwargs["temperature"] = float(self.temperature)
        scanner = ArrayElementScanner()
        chunks: list[str] = []
        usage: dict[str, Any] = {}
        used_model = kwargs["model"]
        stream = await self.client.responses.create(**kwargs)
        async for event in stream:
            etype = getattr(event, "type", "")
            if etype == "response.output_text.delta":
                delta = event.delta
                chunks.append(delta)
                if on_object is not None:
                    for obj in scanner.feed(delta):
                        await on_object(obj)
            elif etype == "response.refusal.done":
                raise RuntimeError(f"model refused: {event.refusal}")
            elif etype == "response.completed":
                resp = event.response
                used_model = getattr(resp, "model", used_model)
                if getattr(resp, "usage", None) is not None:
                    usage = resp.usage.model_dump()
            elif etype in ("response.failed", "error"):
                raise RuntimeError(f"model stream failed: {event}")
        return BackendResult(raw="".join(chunks), model=used_model, usage=usage)
