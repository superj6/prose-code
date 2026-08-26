"""Eval harness: run sync items through any backend and score them.

    .venv/bin/python ml/src/training/evaluate.py --items ml/data/eval_v0.jsonl --backend mock
    .venv/bin/python ml/src/training/evaluate.py --items ml/data/eval_v0.jsonl --override sync.model=gpt-5.6-luna

Item format (JSONL), one sync per line:
    {"id", "language", "prose", "code", "blocks"?, "changed_side", "prose_now"?, "code_now"?, "expected"?}
``prose``/``code`` are the last synced state; ``*_now`` is the user's edited version of the changed
side; ``expected`` is the desired text of the target side (optional: without it only validity,
collateral and latency are reported). Missing ``blocks`` are rebuilt with the production segmenter.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "sync" / "src"))
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv
from prosesync.align import NeedsRegenerate, resegment
from prosesync.backends import get_backend
from prosesync.config import load_config
from prosesync.engine import Engine
from prosesync.interaction_log import InteractionLog
from prosesync.models import (
    Block,
    Change,
    Pair,
    Snapshot,
    SyncRequest,
    other_side,
)

from ml.src.rewards.sync_reward import reward, score


def log_event(**payload: Any) -> None:
    print(json.dumps(payload, default=str), flush=True)


async def run_item(engine: Engine, item: dict[str, Any]) -> dict[str, Any]:
    changed = item["changed_side"]
    target = other_side(changed)
    blocks = [Block.model_validate(b) for b in item["blocks"]] if item.get("blocks") else resegment(
        item["prose"], item["code"], item["language"], engine.min_block_lines
    )
    if blocks is None:
        return {"id": item["id"], "error": "cannot pair paragraphs with code units"}
    prose_now = item.get("prose_now", item["prose"])
    code_now = item.get("code_now", item["code"])
    req = SyncRequest(
        request_id=f"eval-{item['id']}",
        pair=Pair(pair_id=str(item["id"]), language=item["language"], code_path=str(item["id"]), prose=prose_now, code=code_now),
        base=Snapshot(prose=item["prose"], code=item["code"], blocks=blocks),
        change=Change(side=changed),
        other_side_dirty=bool(item.get("other_side_dirty", False)),
    )
    t0 = time.time()
    try:
        resp = await engine.sync(req)
    except NeedsRegenerate as e:
        return {"id": item["id"], "error": f"realign: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"id": item["id"], "error": f"{type(e).__name__}: {e}"}
    latency = int((time.time() - t0) * 1000)
    before = prose_now if target == "prose" else code_now
    after = resp.prose if target == "prose" else resp.code
    # editable set = blocks the engine allowed (reconstructed from the log-equivalent info in warnings is
    # fragile, so recompute it the same way the engine does)
    from prosesync.align import affected, realign

    shifted, hunks, _ = realign(req.base, prose_now, code_now, item["language"], changed, req.other_side_dirty, engine.min_block_lines)
    _, editable = affected(shifted, hunks, changed, int(engine.cfg.sync.context_blocks))
    scores = score(
        language=item["language"], target=target, before=before, after=after, expected=item.get("expected"),
        blocks_before=shifted, blocks_after=resp.blocks, editable=editable, warnings=resp.warnings,
        expected_contains=item.get("expected_contains", ()),
        expected_absent=item.get("expected_absent", ()),
    )
    return {
        "id": item["id"], "target": target, "latency_ms": latency, "model": resp.model, "edits": len(resp.line_edits),
        "warnings": resp.warnings, "scores": scores, "reward": reward(scores), "after": after,
        "tokens_out": resp.usage.get("output_tokens"),
        "tokens_in": resp.usage.get("input_tokens"),
        "cached_in": (resp.usage.get("input_tokens_details") or {}).get("cached_tokens"),
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in results if "error" not in r]
    summary: dict[str, Any] = {"n": len(results), "errors": len(results) - len(ok)}
    keys = sorted({k for r in ok for k in r["scores"]})
    for k in keys:
        vals = [r["scores"][k] for r in ok if k in r["scores"]]
        summary[k] = round(statistics.mean(vals), 3) if vals else None
    if ok:
        tin = sum(r.get("tokens_in") or 0 for r in ok)
        if tin:
            summary["cache_hit"] = round(sum(r.get("cached_in") or 0 for r in ok) / tin, 3)
        lat = sorted(r["latency_ms"] for r in ok)
        summary["latency_p50_ms"] = lat[len(lat) // 2]
        summary["latency_p95_ms"] = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
        summary["reward"] = round(statistics.mean(r["reward"] for r in ok), 3)
    return summary


def markdown(summary: dict[str, Any], label: str) -> str:
    cols = [k for k in summary if k not in ("n", "errors")]
    head = "| run | n | errors | " + " | ".join(cols) + " |"
    sep = "|" + "---|" * (len(cols) + 3)
    row = f"| {label} | {summary['n']} | {summary['errors']} | " + " | ".join(str(summary[c]) for c in cols) + " |"
    return f"{head}\n{sep}\n{row}"


async def main_async(args) -> int:
    load_dotenv(REPO / ".env")
    cfg = load_config(args.config, args.override)
    backend = get_backend(cfg, args.backend)
    engine = Engine(cfg, backend, InteractionLog(None, enabled=False))
    items = [json.loads(ln) for ln in Path(args.items).read_text().splitlines() if ln.strip()]
    if args.limit:
        items = items[: args.limit]
    results = []
    for item in items:
        r = await run_item(engine, item)
        results.append(r)
        log_event(event="item", **{k: v for k, v in r.items() if k != "after"})
    summary = summarize(results)
    label = f"{backend.name}:{args.override or ''}:{cfg.sync.model}"
    out_dir = REPO / "outputs" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out_dir / f"{stamp}.jsonl").write_text("\n".join(json.dumps(r, default=str) for r in results) + "\n")
    (out_dir / f"{stamp}.md").write_text(markdown(summary, label) + "\n")
    log_event(event="summary", **summary)
    print(markdown(summary, label))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--items", required=True)
    p.add_argument("--config", default=None)
    p.add_argument("--override", nargs="*", default=None)
    p.add_argument("--backend", default=None)
    p.add_argument("--limit", type=int, default=0)
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
