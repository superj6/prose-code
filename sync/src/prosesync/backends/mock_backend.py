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

_AFFECTED_LIST_RE = re.compile(r"^Affected blocks: (.*?)\. Editable blocks: (.*?)\.", re.MULTILINE)
_TARGET_RE = re.compile(r"Produce edits to the (PROSE|CODE) side")
_BLOCK_RE = re.compile(r"^\[([a-z]\d*)\]\n(.*?)(?=\n\n\[[a-z]\d*\]\n|\Z)", re.DOTALL | re.MULTILINE)  # a block may span paragraphs


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
        if schema_name == "free_prose":
            code_part = user.split("=== CHILDREN ===", 1)[1]
            paragraphs = [f"Covers {body.split(chr(10))[0].strip()} in this directory." for _bid, body in _BLOCK_RE.findall(code_part)]
            obj = {"summary": "Mock summary of the directory.", "paragraphs": paragraphs}
        elif schema_name == "code_blocks":
            prose_part = user.split("=== PROSE", 1)[1]
            blocks = []
            for bid, body in _BLOCK_RE.findall(prose_part):
                if bid == "s":
                    continue
                first = next((ln.strip() for ln in body.split("\n") if ln.strip()), "")
                blocks.append({"block": bid, "code": f"# {first}\npass"})
            obj = {"blocks": blocks}
        elif schema_name == "paragraphs":
            code_part = user.split("=== CODE", 1)[1]
            paragraphs = []
            for bid, body in _BLOCK_RE.findall(code_part):
                first = next((ln.strip() for ln in body.split("\n") if ln.strip()), "(empty)")
                paragraphs.append({"block": bid, "prose": f"Block {bid}: describes `{first}`"})
            obj = {"summary": "Mock summary of the file.", "paragraphs": paragraphs}
        else:
            target = _TARGET_RE.search(user).group(1).lower()
            source_section = user.split("=== CODE" if target == "prose" else "=== PROSE", 1)[1]
            source_section = source_section.split("\n===", 1)[0]  # stop at the next section header
            m = _AFFECTED_LIST_RE.search(user)
            affected = [b.strip() for b in m.group(1).split(",") if b.strip()]
            editable = [b.strip() for b in m.group(2).split(",") if b.strip()]
            prosetree = "Language: prosetree" in user
            if prosetree:
                affected = [b for b in affected if b != "(none)"]
            else:
                if target == "code" and affected == ["s"]:
                    # summary-driven sync: a real model edits the relevant code blocks; the mock edits the first one
                    affected = [b for b in editable if b != "s"][:1]
                affected = [b for b in affected if b in editable and b != "(none)"]
            bodies = dict(_BLOCK_RE.findall(source_section))
            if prosetree:
                # Free mode: sides are independent partitions. The mock mirrors by index:
                # affected b<i> -> edit p<i> (or the last paragraph), affected p<i>/s -> edit b<i>.
                code_section = user.split("=== CODE", 1)[1].split("\n===", 1)[0]
                prose_section = user.split("=== PROSE", 1)[1].split("\n===", 1)[0]
                code_bodies = dict(_BLOCK_RE.findall(code_section))
                prose_bodies = dict(_BLOCK_RE.findall(prose_section))
                edits = []
                if target == "prose":
                    for bid in affected:
                        pid = "p" + bid[1:]
                        if pid not in editable:
                            pid = [e for e in editable if e != "s"][-1]
                        edits.append({"op": "replace", "block": pid, "text": prose_bodies.get(pid, "").rstrip() + " (updated)", "reason": "mock update"})
                else:
                    for pid in affected:
                        bid = "b" + pid[1:] if pid.startswith("p") else "b1"
                        if bid not in editable:
                            bid = editable[-1]
                        edits.append({"op": "replace", "block": bid, "text": code_bodies.get(bid, "").rstrip() + " (updated)", "reason": "mock update"})
                    # headings in the prose that name children absent from the code side -> new sections
                    known = {ln[3:].strip() for body in code_bodies.values() for ln in body.split("\n") if ln.startswith("## ")}
                    last = editable[-1]
                    extra = ""
                    for body in prose_bodies.values():
                        for ln in body.split("\n"):
                            if ln.startswith("## ") and ln[3:].strip() not in known:
                                name = ln[3:].strip()
                                after = body.split(ln, 1)[1].strip().split("\n")[0] if ln in body else ""
                                extra += f"\n\n## {name}\n{after or 'New child.'}"
                    if extra:
                        base_text = next((e["text"] for e in edits if e["block"] == last), code_bodies.get(last, "").rstrip())
                        edits = [e for e in edits if e["block"] != last] + [{"op": "replace", "block": last, "text": base_text + extra, "reason": "mock create"}]
                obj = {"edits": edits}
                raw = json.dumps(obj)
                for item in obj["edits"]:
                    if on_object is not None:
                        await on_object(item)
                return BackendResult(raw=raw, model="mock", usage={"input_tokens": 0, "output_tokens": 0})
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
