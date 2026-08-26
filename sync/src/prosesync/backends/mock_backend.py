"""Deterministic backend for tests and for developing the extension without an API key.

* generate: one paragraph per block, ``"## block bN\\nDescribes: <first non-empty line>"``.
* sync: for every AFFECTED block, replaces the target text with a stub derived from the source
  block's first line, so the mapping/apply path is exercised end to end.
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from ..models import BackendResult

_AFFECTED_LIST_RE = re.compile(r"^Affected blocks: (.*?)\.", re.MULTILINE)
_TARGET_RE = re.compile(r"Produce edits to the (PROSE|CODE) side")
_BLOCK_RE = re.compile(r"^\[(b\d+)\]\n(.*?)(?=\n\n\[b\d+|\n\n|\Z)", re.DOTALL | re.MULTILINE)


class MockBackend:
    name = "mock"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        schema_name: str,
        on_object: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        model: str | None = None,
        on_partial: Callable[[str | None, str], Awaitable[None]] | None = None,
        cache_key: str | None = None,
    ) -> BackendResult:
        user = "\n".join(m["content"] for m in messages if m["role"] == "user")  # repair rounds append a user turn
        self.calls.append({"messages": messages, "schema_name": schema_name})
        if schema_name == "paragraphs":
            code_part = user.split("=== CODE ===", 1)[1]
            paragraphs = []
            for bid, body in _BLOCK_RE.findall(code_part):
                first = next((ln.strip() for ln in body.split("\n") if ln.strip()), "(empty)")
                paragraphs.append({"block": bid, "prose": f"Block {bid}: describes `{first}`"})
            obj = {"paragraphs": paragraphs}
        else:
            target = _TARGET_RE.search(user).group(1).lower()
            source_section = user.split("=== CODE ===" if target == "prose" else "=== PROSE ===", 1)[1]
            source_section = source_section.split("\n===", 1)[0]  # stop at the next section header
            listed = _AFFECTED_LIST_RE.search(user).group(1)
            affected = [b.strip() for b in listed.split(",") if b.strip().startswith("b")]
            bodies = dict(_BLOCK_RE.findall(source_section))
            edits = []
            for bid in affected:
                body = bodies.get(bid, "")
                first = next((ln.strip() for ln in body.split("\n") if ln.strip()), "")
                if target == "prose":
                    text = f"Block {bid}: describes `{first}` (updated)"
                else:
                    text = f"# {first}\npass"
                edits.append({"op": "replace", "block": bid, "text": text, "reason": "mock update"})
            obj = {"edits": edits}
        raw = json.dumps(obj)
        for item in next(iter(obj.values())):
            if on_partial is not None and "text" in item:
                await on_partial(item["block"], item["text"][: len(item["text"]) // 2])
                await on_partial(item["block"], item["text"])
            if on_object is not None:
                await on_object(item)
        return BackendResult(raw=raw, model="mock", usage={"input_tokens": 0, "output_tokens": 0})
