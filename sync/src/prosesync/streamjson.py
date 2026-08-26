"""Incremental extraction of completed array elements from a streamed JSON object.

Feed text chunks in; every time an element of the top-level ``{"<key>": [ ... ]}`` array is
complete, it is parsed and yielded. Robust to strings containing braces/brackets.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

_TEXT_RE = re.compile(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)')
_BLOCK_RE = re.compile(r'"block"\s*:\s*"([^"\\]*)"')


def partial_text(element_so_far: str) -> tuple[str | None, str] | None:
    """(block id if seen yet, decoded ``text`` so far) for a partially streamed edit object."""
    m = _TEXT_RE.search(element_so_far)
    if not m:
        return None
    raw = m.group(1)
    if raw.endswith("\\") and not raw.endswith("\\\\"):
        raw = raw[:-1]  # dangling escape: wait for the next char
    try:
        text = json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return None
    b = _BLOCK_RE.search(element_so_far)
    return (b.group(1) if b else None), text


class ArrayElementScanner:
    def __init__(self) -> None:
        self.depth = 0          # nesting depth of {}/[]
        self.in_string = False
        self.escape = False
        self.buf: list[str] = []
        self.collecting = False
        self.array_depth: int | None = None  # depth at which the top-level array's elements live
        self.done: list[dict[str, Any]] = []

    def current(self) -> str:
        """The element being collected so far (empty when between elements)."""
        return "".join(self.buf) if self.collecting else ""

    def feed(self, chunk: str) -> Iterator[dict[str, Any]]:
        for ch in chunk:
            if self.collecting:
                self.buf.append(ch)
            if self.in_string:
                if self.escape:
                    self.escape = False
                elif ch == "\\":
                    self.escape = True
                elif ch == '"':
                    self.in_string = False
                continue
            if ch == '"':
                self.in_string = True
            elif ch in "{[":
                self.depth += 1
                if ch == "[" and self.array_depth is None and self.depth == 2:
                    self.array_depth = self.depth
                elif self.array_depth is not None and self.depth == self.array_depth + 1 and not self.collecting:
                    self.collecting = True
                    self.buf = [ch]
            elif ch in "}]":
                self.depth -= 1
                if self.collecting and self.array_depth is not None and self.depth == self.array_depth:
                    text = "".join(self.buf)
                    self.collecting = False
                    self.buf = []
                    try:
                        obj = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict):
                        self.done.append(obj)
                        yield obj
