"""Incremental extraction of completed array elements from a streamed JSON object.

Feed text chunks in; every time an element of the top-level ``{"<key>": [ ... ]}`` array is
complete, it is parsed and yielded. Robust to strings containing braces/brackets.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any


class ArrayElementScanner:
    def __init__(self) -> None:
        self.depth = 0          # nesting depth of {}/[]
        self.in_string = False
        self.escape = False
        self.buf: list[str] = []
        self.collecting = False
        self.array_depth: int | None = None  # depth at which the top-level array's elements live
        self.done: list[dict[str, Any]] = []

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
