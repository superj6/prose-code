"""Direct Preference Optimization on (chosen, rejected) completion pairs, LoRA policy vs frozen
reference (the SFT adapter). Same encoding as sft.py (loss on completion tokens only).

    python ml/src/training/dpo.py --config configs/modal_dpo.yaml --adapter /data/outputs/sft/final \
        --pairs /data/datasets/prosesync/v1/pairs.jsonl --out /data/outputs/dpo
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F

REPO = Path(__file__).resolve().parents[3]
for _p in (REPO / "sync" / "src", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ml.src.models.loader import (
    attach_lora,
    load_adapter,
    load_base,
    load_tokenizer,
    save_adapter,
)
from ml.src.training.common import (
    load_config,
    log_event,
    resolve_device,
    set_seed,
)
from ml.src.training.sft import collate, encode_example, read_examples


def seq_logprob(model, batch: dict[str, torch.Tensor]) -> torch.Tensor:
    """Sum of log-probs of the label tokens (labels == -100 are masked), one value per sequence."""
    out = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"])
    logits = out.logits[:, :-1, :]
    labels = batch["labels"][:, 1:]
    mask = labels != -100
    logp = torch.log_softmax(logits.float(), dim=-1)
    tok_lp = torch.gather(logp, 2, labels.clamp(min=0).unsqueeze(-1)).squeeze(-1)
    return (tok_lp * mask).sum(dim=1)


def encode_pair(tok, pair: dict[str, Any], max_length: int):
    c = encode_example(tok, {"messages": pair["messages"], "completion": pair["chosen"]}, max_length)
    r = encode_example(tok, {"messages": pair["messages"], "completion": pair["rejected"]}, max_length)
    return (c, r) if c is not None and r is not None else None


def train(cfg, adapter: str | None, pairs_path: str, out_dir: Path) -> dict[str, Any]:
    set_seed(int(cfg.seed))
    device = resolve_device(cfg.get("device"))
    tok = load_tokenizer(cfg.model.name)
    base = load_base(cfg, device)
    if adapter:
        policy = load_adapter(base, adapter)
        for n, p in policy.named_parameters():
            p.requires_grad = "lora_" in n
    else:
        policy = attach_lora(base, cfg)
    reference = load_adapter(load_base(cfg, device), adapter).eval() if adapter else None  # None = base model
    ref_model = reference if reference is not None else load_base(cfg, device).eval()
    for p in ref_model.parameters():
        p.requires_grad = False
    pairs = [e for e in (encode_pair(tok, pr, int(cfg.dpo.max_length)) for pr in read_examples(pairs_path, cfg.dpo.get("max_pairs"))) if e]
    log_event(event="data", pairs=len(pairs))
    cf = collate(tok.pad_token_id)
    params = [p for p in policy.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=float(cfg.dpo.lr), weight_decay=0.0)
    beta = float(cfg.dpo.beta)
    bs = int(cfg.dpo.batch_size)
    total_steps = min(int(cfg.dpo.get("max_steps") or 10**9), math.ceil(len(pairs) / bs) * int(cfg.dpo.epochs))
    autocast_dtype = cfg.train.get("autocast_dtype")
    autocast = (lambda: torch.autocast("cuda", dtype=getattr(torch, autocast_dtype))) if (device.type == "cuda" and autocast_dtype) else (lambda: torch.autocast("cpu", enabled=False))
    policy.train()
    step, t0 = 0, time.time()
    g = torch.Generator().manual_seed(int(cfg.seed))
    while step < total_steps:
        order = torch.randperm(len(pairs), generator=g).tolist()
        for i in range(0, len(order), bs):
            idx = order[i : i + bs]
            chosen = {k: v.to(device) for k, v in cf([pairs[j][0] for j in idx]).items()}
            rejected = {k: v.to(device) for k, v in cf([pairs[j][1] for j in idx]).items()}
            with torch.no_grad(), autocast():
                ref_c, ref_r = seq_logprob(ref_model, chosen), seq_logprob(ref_model, rejected)
            with autocast():
                pol_c, pol_r = seq_logprob(policy, chosen), seq_logprob(policy, rejected)
            margin = beta * ((pol_c - ref_c) - (pol_r - ref_r))
            loss = -F.logsigmoid(margin).mean()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, float(cfg.dpo.grad_clip))
            opt.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            if step % int(cfg.dpo.log_interval) == 0 or step == total_steps:
                acc = float((margin > 0).float().mean())
                log_event(event="train", step=step, loss=round(float(loss), 4), reward_acc=round(acc, 3), margin=round(float(margin.mean()), 3), elapsed_s=round(time.time() - t0, 1))
            if step >= total_steps:
                break
    save_adapter(policy, out_dir / "final", tok)
    summary = {"steps": step, "pairs": len(pairs), "out": str(out_dir / "final")}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    log_event(event="done", **summary)
    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--override", nargs="*", default=None)
    p.add_argument("--adapter", default=None, help="SFT adapter to start from (policy init + reference)")
    p.add_argument("--pairs", required=True)
    p.add_argument("--out", default=None)
    args = p.parse_args()
    cfg = load_config(args.config, args.override)
    out_dir = Path(args.out or cfg.dpo.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    train(cfg, args.adapter, args.pairs, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
