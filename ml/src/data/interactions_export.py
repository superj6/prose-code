"""Turn the interaction log (~/.prosecode/logs/*.jsonl) into dataset records.

    .venv/bin/python ml/src/data/interactions_export.py --out ml/data/interactions.jsonl [--logs DIR]

Joins ``sync`` records with their ``feedback`` (accepted / modified / reverted). Reverted syncs are
kept with meta.outcome so they can serve as DPO rejections; ``modified`` ones keep the model's
edits as the label until the extension reports the user's final block text (TODO in
feedbackTracker). Mock/no-op/errored syncs are dropped; exact duplicates are deduped.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from ml.src.data.records import record_id, validate, write_jsonl


def load_logs(log_dir: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(os.path.expanduser(log_dir), "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    return rows


def export(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    feedback = {r["sync_id"]: r for r in rows if r.get("kind") == "feedback"}
    stats = {"sync": 0, "dropped_mock": 0, "dropped_noop": 0, "dropped_invalid": 0, "dup": 0, "kept": 0}
    seen: set[str] = set()
    out = []
    for r in rows:
        if r.get("kind") != "sync":
            continue
        stats["sync"] += 1
        if r.get("model") in ("mock", "openai") or not r.get("model"):
            stats["dropped_mock"] += 1
            continue
        if not r.get("edits_applied") or r.get("error"):
            stats["dropped_noop"] += 1
            continue
        changed = r["changed_side"]
        rec = {
            "source": "interaction",
            "language": r["language"],
            "prompt_version": r.get("prompt_version", "v1"),
            # snapshot = current text with the user's hunks reversed is not stored; the log keeps
            # the *current* texts (prose_before/code_before) plus hunks vs the snapshot, so rebuild
            # the snapshot side from the hunks.
            "prose": _unapply(r["prose_before"], r["hunks"]) if changed == "prose" else r["prose_before"],
            "code": _unapply(r["code_before"], r["hunks"]) if changed == "code" else r["code_before"],
            "blocks": _snapshot_blocks(r),
            "changed_side": changed,
            f"{changed}_now": r["prose_before"] if changed == "prose" else r["code_before"],
            "other_side_dirty": bool(r.get("other_side_dirty")),
            "target_edits": [{k: e.get(k) for k in ("op", "block", "text", "reason")} for e in r["edits_applied"]],
            "prose_after": r["prose_after"],
            "code_after": r["code_after"],
            "meta": {
                "sync_id": r.get("sync_id"), "model": r.get("model"), "latency_ms": r.get("latency_ms"),
                "window": r.get("window"), "affected": r.get("affected"), "editable": r.get("editable"),
                "outcome": (feedback.get(r.get("sync_id")) or {}).get("outcome"), "ts": r.get("ts"),
            },
        }
        if rec["other_side_dirty"]:
            other = "code" if changed == "prose" else "prose"
            rec[f"{other}_now"] = r["code_before"] if other == "code" else r["prose_before"]
            rec[other] = _unapply(rec[f"{other}_now"], r.get("other_hunks") or [])
        err = validate(rec)
        if err:
            stats["dropped_invalid"] += 1
            continue
        rid = record_id(rec)
        if rid in seen:
            stats["dup"] += 1
            continue
        seen.add(rid)
        rec["id"] = rid
        out.append(rec)
        stats["kept"] += 1
    return out, stats


def _snapshot_blocks(r: dict[str, Any]) -> list[dict[str, Any]]:
    """blocks_before in the log are already shifted to the *current* text; the snapshot's own map is
    recovered by unshifting through the hunks. For training we only need a consistent partition of
    the snapshot texts, so re-derive it from the unapplied text lengths instead."""
    from prosesync import blocks as B

    changed = r["changed_side"]
    prose = _unapply(r["prose_before"], r["hunks"]) if changed == "prose" else r["prose_before"]
    code = _unapply(r["code_before"], r["hunks"]) if changed == "code" else r["code_before"]
    blocks = [dict(b) for b in r["blocks_before"]]
    hunks = [dict(h) for h in r["hunks"]]
    inverse = [{"old_start": h["new_start"], "old_lines": h["new_lines"], "new_start": h["old_start"], "new_lines": h["old_lines"]} for h in hunks]
    from prosesync.models import Block, Hunk

    shifted = B.shift_ranges([Block(**b) for b in blocks], [Hunk(**h) for h in inverse], changed)
    out = [b.model_dump() for b in shifted]
    if B.check_partition(shifted, "prose", len(B.split_lines(prose))) or B.check_partition(shifted, "code", len(B.split_lines(code))):
        from prosesync.align import resegment

        rebuilt = resegment(prose, code, r["language"])
        out = [b.model_dump() for b in rebuilt] if rebuilt else out
    return out


def _unapply(current: str, hunks: list[dict[str, Any]]) -> str:
    """Reverse the hunks (new -> old) to recover the snapshot text."""
    from prosesync.blocks import join_lines, split_lines

    lines = split_lines(current)
    for h in sorted(hunks, key=lambda h: h["new_start"], reverse=True):
        old_lines = split_lines(h.get("old_text", ""))
        lines[h["new_start"] : h["new_start"] + h["new_lines"]] = old_lines
    return join_lines(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--logs", default="~/.prosecode/logs")
    p.add_argument("--out", default=str(REPO / "ml" / "data" / "interactions.jsonl"))
    args = p.parse_args()
    sys.path.insert(0, str(REPO / "sync" / "src"))
    records, stats = export(load_logs(args.logs))
    n = write_jsonl(Path(args.out), records)
    print(json.dumps({"written": n, **stats}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
