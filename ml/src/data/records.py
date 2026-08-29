"""The dataset record: one sync example. Produced by interactions_export.py (real usage) and
perturb.py (synthetic); consumed by dataset.py (training) and the eval harness.

    {id, source, language, prompt_version, prose, code, blocks, changed_side, prose_now?, code_now?,
     other_side_dirty, target_edits: [Edit], prose_after, code_after, meta: {...}}

``prose``/``code``/``blocks`` are the last synced snapshot; ``*_now`` the user's edited version of a
side (defaults to the snapshot); ``target_edits`` the label (block ops on the target side).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any


def record_id(rec: dict[str, Any]) -> str:
    key = json.dumps([rec["code"], rec["prose"], rec.get("code_now"), rec.get("prose_now"), rec["changed_side"]], sort_keys=True)
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def validate(rec: dict[str, Any]) -> str | None:
    for k in ("language", "prose", "code", "blocks", "changed_side", "target_edits", "prose_after", "code_after"):
        if k not in rec:
            return f"missing {k}"
    if rec["changed_side"] not in ("prose", "code"):
        return "bad changed_side"
    for e in rec["target_edits"]:
        if e.get("op") not in ("replace", "delete") or not e.get("block"):
            return f"bad edit {e}"
    changed_now = rec.get(f"{rec['changed_side']}_now")
    if changed_now is None or changed_now == rec[rec["changed_side"]]:
        return "no change on the changed side"
    return None


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)
