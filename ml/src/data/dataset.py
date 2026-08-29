"""Render dataset records into chat examples with the *production* prompt builder.

    .venv/bin/python ml/src/data/dataset.py stats ml/data/interactions.jsonl
    .venv/bin/python ml/src/data/dataset.py render ml/data/interactions.jsonl > /tmp/examples.jsonl

Each rendered example is {"messages": [system, user], "completion": "<json edits>", "meta": {...}}.
The user turn is built by ``prosesync.prompts.build_sync_messages`` from the same realign /
affected / window logic the server uses, so train-time and serve-time prompts are identical.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "sync" / "src"))
sys.path.insert(0, str(REPO))

from prosesync.align import affected, realign
from prosesync.config import load_config
from prosesync.models import Block, Snapshot, other_side
from prosesync.prompts import build_sync_messages, window_ids

from ml.src.data.records import read_jsonl


def render(rec: dict[str, Any], cfg) -> dict[str, Any] | None:
    changed = rec["changed_side"]
    target = other_side(changed)
    base = Snapshot(prose=rec["prose"], code=rec["code"], blocks=[Block(**b) for b in rec["blocks"]])
    prose_now = rec.get("prose_now", rec["prose"])
    code_now = rec.get("code_now", rec["code"])
    try:
        blocks, hunks, other_hunks, base_blocks = realign(base, prose_now, code_now, rec["language"], changed, bool(rec.get("other_side_dirty")), int(cfg.segment.min_block_lines))
    except Exception:  # noqa: BLE001 - unrenderable record is skipped, reported by stats
        return None
    core, editable = affected(base_blocks, hunks, changed, int(cfg.sync.context_blocks))
    protected: list[str] = []
    if rec.get("other_side_dirty") and other_hunks:
        protected, _ = affected(base_blocks, other_hunks, target, 0)
        editable = [b for b in editable if b not in protected]
    full = window_ids(blocks, editable, int(cfg.window.max_full_blocks), int(cfg.window.radius))
    messages = build_sync_messages(
        language=rec["language"], prose=prose_now, code=code_now, blocks=blocks, changed=changed, hunks=hunks,
        affected=core, editable=editable, other_side_dirty=bool(rec.get("other_side_dirty")), other_hunks=other_hunks,
        version=rec.get("prompt_version", str(cfg.sync.prompt_version)), full=full, protected=protected,
    )
    edits = [{"op": e["op"], "block": e["block"], "text": e.get("text"), "reason": e.get("reason") or ""} for e in rec["target_edits"]]
    return {"messages": messages, "completion": json.dumps({"edits": edits}, ensure_ascii=False), "meta": {"id": rec["id"], "source": rec["source"], "language": rec["language"], **(rec.get("meta") or {})}}


def stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    c = Counter()
    for r in records:
        c[f"lang:{r['language']}"] += 1
        c[f"side:{r['changed_side']}"] += 1
        c[f"source:{r['source']}"] += 1
        c[f"edits:{min(len(r['target_edits']), 4)}"] += 1
        if r.get("other_side_dirty"):
            c["both_dirty"] += 1
        oc = (r.get("meta") or {}).get("outcome")
        if oc:
            c[f"outcome:{oc}"] += 1
    return {"n": len(records), **dict(sorted(c.items()))}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["stats", "render"])
    p.add_argument("path")
    p.add_argument("--config", default=None)
    args = p.parse_args()
    records = list(read_jsonl(Path(args.path)))
    if args.cmd == "stats":
        print(json.dumps(stats(records), indent=1))
        return 0
    cfg = load_config(args.config)
    skipped = 0
    for r in records:
        ex = render(r, cfg)
        if ex is None:
            skipped += 1
            continue
        print(json.dumps(ex, ensure_ascii=False))
    print(json.dumps({"rendered": len(records) - skipped, "skipped": skipped}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
