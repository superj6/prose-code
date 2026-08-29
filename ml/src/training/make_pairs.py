"""Sample K completions per training prompt from an SFT adapter, score them with the automatic
sync reward, and emit DPO pairs (best vs worst when the reward gap is large enough).

    python ml/src/training/make_pairs.py --config configs/modal_dpo.yaml --adapter /data/outputs/sft/final \
        --examples /data/datasets/prosesync/v1/train.jsonl --records /data/datasets/prosesync/synth.jsonl \
        --out /data/datasets/prosesync/v1/pairs.jsonl [--k 4] [--limit 500]

Scoring re-applies each candidate's edits to the record's documents through the production apply
path (unknown blocks / bad JSON -> reward 0), then uses ml.src.rewards.sync_reward.reward with the
record's ground-truth target text as ``expected``. Real interactions with a `reverted` outcome can
be added as rejections by the caller (see interactions_export).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import torch

REPO = Path(__file__).resolve().parents[3]
for _p in (REPO / "sync" / "src", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from prosesync.align import affected, realign
from prosesync.apply import ApplyError, DocState
from prosesync.models import Block, Edit, Snapshot, other_side

from ml.src.data.records import read_jsonl
from ml.src.models.loader import load_adapter, load_base, load_tokenizer
from ml.src.rewards.sync_reward import reward, score
from ml.src.training.common import (
    load_config,
    log_event,
    resolve_device,
    set_seed,
)


def score_completion(rec: dict[str, Any], completion: str, cfg) -> float:
    """Reward of a raw model completion for a dataset record (0 when it cannot be applied)."""
    try:
        edits = [Edit.model_validate(e) for e in json.loads(completion)["edits"]]
    except Exception:  # noqa: BLE001 - malformed JSON / schema
        return 0.0
    changed = rec["changed_side"]
    target = other_side(changed)
    base = Snapshot(prose=rec["prose"], code=rec["code"], blocks=[Block(**b) for b in rec["blocks"]])
    prose_now, code_now = rec.get("prose_now", rec["prose"]), rec.get("code_now", rec["code"])
    try:
        blocks, hunks, _ = realign(base, prose_now, code_now, rec["language"], changed, False, int(cfg.segment.min_block_lines))
    except Exception:  # noqa: BLE001
        return 0.0
    _, editable = affected(blocks, hunks, changed, int(cfg.sync.context_blocks))
    state = DocState(prose_now, code_now, blocks, rec["language"], int(cfg.segment.min_block_lines))
    warnings = []
    for e in edits:
        if e.block not in editable:
            warnings.append(f"edit to {e.block} rejected")
            continue
        try:
            state.apply(e, target)
        except ApplyError as err:
            warnings.append(f"rejected: {err}")
    before = prose_now if target == "prose" else code_now
    after = state.text(target)
    expected = rec["prose_after"] if target == "prose" else rec["code_after"]
    s = score(language=rec["language"], target=target, before=before, after=after, expected=expected,
              blocks_before=blocks, blocks_after=state.blocks(), editable=editable, warnings=warnings)
    return reward(s)


@torch.no_grad()
def sample(model, tok, messages: list[dict[str, str]], k: int, max_new_tokens: int, temperature: float, device) -> list[str]:
    prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True) if tok.chat_template else "\n".join(f"{m['role']}: {m['content']}" for m in messages) + "\nassistant: "
    enc = tok(prompt, return_tensors="pt", add_special_tokens=False).to(device)
    out = model.generate(**enc, do_sample=True, temperature=temperature, top_p=0.95, max_new_tokens=max_new_tokens, num_return_sequences=k, pad_token_id=tok.pad_token_id)
    return [tok.decode(seq[enc["input_ids"].shape[1]:], skip_special_tokens=True) for seq in out]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--adapter", required=True)
    p.add_argument("--examples", required=True, help="rendered examples (dataset.py render / prepare.py)")
    p.add_argument("--records", required=True, help="the records the examples were rendered from (for scoring)")
    p.add_argument("--out", required=True)
    p.add_argument("--k", type=int, default=4)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--margin", type=float, default=0.2)
    p.add_argument("--max-new-tokens", type=int, default=1024)
    p.add_argument("--temperature", type=float, default=0.8)
    args = p.parse_args()
    cfg = load_config(args.config)
    set_seed(int(cfg.seed))
    device = resolve_device(cfg.get("device"))
    tok = load_tokenizer(cfg.model.name)
    model = load_adapter(load_base(cfg, device), args.adapter).eval()
    records = {r["id"]: r for r in read_jsonl(Path(args.records))}
    examples = list(read_jsonl(Path(args.examples)))[: args.limit or None]
    pairs, stats = [], {"examples": 0, "no_record": 0, "pairs": 0, "mean_best": 0.0}
    with open(args.out, "w", encoding="utf-8") as f:
        for ex in examples:
            rec = records.get(ex["meta"]["id"])
            if rec is None:
                stats["no_record"] += 1
                continue
            stats["examples"] += 1
            cands = sample(model, tok, ex["messages"], args.k, args.max_new_tokens, args.temperature, device)
            scored = sorted(((score_completion(rec, c, cfg), c) for c in cands), key=lambda t: t[0], reverse=True)
            best, worst = scored[0], scored[-1]
            stats["mean_best"] += best[0]
            if best[0] - worst[0] >= args.margin:
                pairs.append(1)
                stats["pairs"] += 1
                f.write(json.dumps({"messages": ex["messages"], "chosen": best[1], "rejected": worst[1], "chosen_reward": best[0], "rejected_reward": worst[0], "meta": ex["meta"]}, ensure_ascii=False) + "\n")
    stats["mean_best"] = round(stats["mean_best"] / max(1, stats["examples"]), 3)
    log_event(event="pairs", out=args.out, **stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
