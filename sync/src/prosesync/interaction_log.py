"""JSONL log of every sync/generate/feedback event. This is the future training set."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


class InteractionLog:
    def __init__(self, directory: str | None, enabled: bool = True):
        self.enabled = enabled and bool(directory)
        self.dir = Path(os.path.expanduser(directory)) if directory else None

    def write(self, kind: str, record: dict[str, Any]) -> None:
        if not self.enabled or self.dir is None:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / (time.strftime("%Y-%m-%d") + ".jsonl")
        record = {"kind": kind, "ts": time.time(), **record}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=_default) + "\n")


def _default(o: Any):
    if hasattr(o, "model_dump"):
        return o.model_dump()
    return str(o)
