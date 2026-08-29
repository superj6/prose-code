"""Synthetic perturbation quads: (P0, C0) -> edit one side -> production sync -> labelled record.

    .venv/bin/python ml/src/data/perturb.py --manifest ml/data/seed/manifest.jsonl --out ml/data/synth.jsonl \
        [--per-file 2] [--limit 20] [--backend mock]

For each seed file: generate prose with the production generator; then for N randomly chosen
blocks, ask the model for a realistic small edit on one side (code or prose, alternating), run the
production Engine.sync to obtain the paired edits on the other side, verify (syntax on the code
side, untouched blocks identical is guaranteed by the engine), and write a dataset record whose
label is exactly what the server would have produced. Records therefore match the serving
distribution by construction; real interactions and git-mined edits add diversity later.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "sync" / "src"))
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv
from prosesync import blocks as B
from prosesync.align import NeedsRegenerate
from prosesync.apply import apply_edits
from prosesync.backends import get_backend
from prosesync.config import load_config
from prosesync.engine import Engine
from prosesync.interaction_log import InteractionLog
from prosesync.models import (
    Change,
    Edit,
    Pair,
    Snapshot,
    SyncRequest,
    other_side,
)
from prosesync.prompts import render_blocks, system_prompt
from prosesync.prompts.schema import PERTURB_SCHEMA
from prosesync.verify.treesitter import first_error

from ml.src.data.records import (
    read_jsonl,
    record_id,
    validate,
    write_jsonl,
)


async def propose_edit(engine: Engine, language: str, prose: str, code: str, blocks, side: str, block_id: str) -> dict[str, Any] | None:
    user = [
        f"Language: {language}",
        "",
        "=== CODE ===",
        render_blocks(code, blocks, "code"),
        "",
        "=== PROSE ===",
        render_blocks(prose, blocks, "prose"),
        "",
        (
            f"Propose one realistic small change to the {side.upper()} side of block {block_id}. "
            f'Return JSON: {{"block": "{block_id}", "text": "<full new {side} text of the block>", "label": "<short label>"}}'
        ),
    ]
    messages = [{"role": "system", "content": system_prompt("perturb", "v1")}, {"role": "user", "content": "\n".join(user)}]
    if engine.backend.name == "mock":
        parts = dict(zip([b.id for b in blocks], B.block_text(prose if side == "prose" else code, blocks, side)))
        body = parts[block_id].rstrip("\n")
        text = body + ("\n    # perturbed" if side == "code" else " Also validate the input.")
        return {"block": block_id, "text": text, "label": "mock"}
    result = await engine.backend.complete_json(messages, PERTURB_SCHEMA, "perturb")
    try:
        obj = json.loads(result.raw)
    except json.JSONDecodeError:
        return None
    return obj if obj.get("block") == block_id and obj.get("text") else None


async def make_records(engine: Engine, item: dict[str, Any], per_file: int, rng: random.Random) -> list[dict[str, Any]]:
    code = Path(item["file"]).read_text(encoding="utf-8")
    language = item["language"]
    gen = await engine.generate(code, language, item["file"])
    if len(gen.blocks) < 2:
        return []
    prose, blocks = gen.prose, gen.blocks
    candidates = [b.id for b in blocks[1:]]  # skip the import/header block
    rng.shuffle(candidates)
    out = []
    for i, bid in enumerate(candidates[:per_file]):
        side = "code" if i % 2 == 0 else "prose"
        proposal = await propose_edit(engine, language, prose, code, blocks, side, bid)
        if not proposal:
            continue
        edited_prose, edited_code, _, _ = apply_edits(prose, code, blocks, [Edit(op="replace", block=bid, text=proposal["text"])], side, language)
        if side == "code":
            ok = first_error(language, edited_code)
            if ok is not None and not ok[0]:
                continue  # the proposed code edit itself is broken
        req = SyncRequest(
            request_id=f"synth-{item['id']}-{bid}-{side}",
            pair=Pair(mode="paired", pair_id=f"synth-{item['id']}", language=language, code_path=item["file"], prose=edited_prose, code=edited_code),
            base=Snapshot(prose=prose, code=code, blocks=blocks), change=Change(side=side),
        )
        try:
            resp = await engine.sync(req)
        except NeedsRegenerate:
            continue
        if not resp.line_edits or any("rejected" in w for w in resp.warnings) or any(le.block == "*" for le in resp.line_edits):
            continue
        target = other_side(side)
        if target == "code":
            ok = first_error(language, resp.code)
            if ok is not None and not ok[0]:
                continue
        rec = {
            "source": "synthetic", "language": language, "prompt_version": engine.prompt_version,
            "prose": prose, "code": code, "blocks": [b.model_dump() for b in blocks], "changed_side": side,
            f"{side}_now": edited_prose if side == "prose" else edited_code, "other_side_dirty": False,
            "target_edits": [{"op": "replace" if le.new_text else "delete", "block": le.block, "text": le.new_text or None, "reason": le.reason or ""} for le in resp.line_edits],
            "prose_after": resp.prose, "code_after": resp.code,
            "meta": {"seed": item["id"], "block": bid, "label": proposal.get("label"), "model": resp.model, "latency_ms": resp.latency_ms},
        }
        if validate(rec):
            continue
        rec["id"] = record_id(rec)
        out.append(rec)
    return out


async def main_async(args) -> int:
    load_dotenv(REPO / ".env")
    cfg = load_config(args.config, args.override)
    engine = Engine(cfg, get_backend(cfg, args.backend), InteractionLog(None, enabled=False))
    rng = random.Random(args.seed)
    items = list(read_jsonl(Path(args.manifest)))[: args.limit or None]
    records: list[dict[str, Any]] = []
    for it in items:
        try:
            recs = await make_records(engine, it, args.per_file, rng)
        except Exception as e:  # noqa: BLE001 - one bad seed must not kill the run
            print(json.dumps({"seed": it["id"], "error": f"{type(e).__name__}: {e}"}), file=sys.stderr)
            continue
        records.extend(recs)
        print(json.dumps({"seed": it["id"], "language": it["language"], "records": len(recs)}), file=sys.stderr)
    n = write_jsonl(Path(args.out), records)
    print(json.dumps({"written": n, "seeds": len(items)}))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--per-file", type=int, default=2)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--config", default=None)
    p.add_argument("--override", nargs="*", default=None)
    p.add_argument("--backend", default=None)
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
