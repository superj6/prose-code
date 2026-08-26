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

_AFFECTED_RE = re.compile(r"^\[(b\d+) AFFECTED\]\n(.*?)(?=\n\n\[b\d+|\n\n===|\Z)", re.DOTALL | re.MULTILINE)
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
    ) -> BackendResult:
        user = messages[-1]["content"]
        self.calls.append({"messages": messages, "schema_name": schema_name})
        if schema_name == "paragraphs":
            code_part = user.split("=== CODE ===", 1)[1]
            paragraphs = []
            for bid, body in _BLOCK_RE.findall(code_part):
                first = next((ln.strip() for ln in body.split("\n") if ln.strip()), "(empty)")
                paragraphs.append({"block": bid, "prose": f"## block {bid}\nDescribes: `{first}`"})
            obj = {"paragraphs": paragraphs}
        else:
            target = _TARGET_RE.search(user).group(1).lower()
            source_section = user.split("=== CODE ===" if target == "prose" else "=== PROSE ===", 1)[1]
            source_section = source_section.split("\n===", 1)[0]  # stop at the next section header
            edits = []
            for bid, body in _AFFECTED_RE.findall(source_section):
                first = next((ln.strip() for ln in body.split("\n") if ln.strip()), "")
                if target == "prose":
                    text = f"## block {bid}\nDescribes: `{first}` (updated)"
                else:
                    text = f"# {first}\npass"
                edits.append({"op": "replace", "block": bid, "text": text, "reason": "mock update"})
            obj = {"edits": edits}
        raw = json.dumps(obj)
        if on_object is not None:
            for item in next(iter(obj.values())):
                await on_object(item)
        return BackendResult(raw=raw, model="mock", usage={"input_tokens": 0, "output_tokens": 0})
