"""LoRA supervised fine-tuning on rendered sync examples.

    .venv/bin/python ml/src/training/sft.py --config configs/local_smoke.yaml
    modal run ml/src/modal_app.py --job sft --config configs/modal_sft.yaml

Input: JSONL of {"messages": [system, user], "completion": "<json>"} (see ml/src/data/dataset.py
render). Loss only on the completion tokens. Saves a LoRA adapter (+ tokenizer) every
``save_every`` steps and at the end; evaluates held-out loss every ``eval_every`` steps.
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
from torch.utils.data import DataLoader, Dataset

REPO = Path(__file__).resolve().parents[3]
for _p in (REPO / "sync" / "src", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ml.src.models.loader import (
    attach_lora,
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


def read_examples(path: str | Path, limit: int | None = None) -> list[dict[str, Any]]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
                if limit and len(out) >= limit:
                    break
    return out


def encode_example(tok, ex: dict[str, Any], max_length: int) -> dict[str, torch.Tensor] | None:
    """Chat-template the prompt, append the completion; labels = -100 on prompt tokens."""
    messages = ex["messages"]
    if hasattr(tok, "apply_chat_template") and tok.chat_template:
        prompt_text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    else:  # tiny test tokenizers without a template
        prompt_text = "\n".join(f"{m['role']}: {m['content']}" for m in messages) + "\nassistant: "
    prompt_ids = tok(prompt_text, add_special_tokens=False)["input_ids"]
    completion_ids = tok(ex["completion"], add_special_tokens=False)["input_ids"] + [tok.eos_token_id]
    if len(prompt_ids) + len(completion_ids) > max_length:
        # Keep the whole completion; truncate the prompt head only if it still leaves the instructions.
        keep = max_length - len(completion_ids)
        if keep < 64:
            return None
        prompt_ids = prompt_ids[-keep:]
    ids = prompt_ids + completion_ids
    labels = [-100] * len(prompt_ids) + completion_ids
    return {"input_ids": torch.tensor(ids), "labels": torch.tensor(labels)}


class SftDataset(Dataset):
    def __init__(self, tok, examples: list[dict[str, Any]], max_length: int):
        self.items = [e for e in (encode_example(tok, ex, max_length) for ex in examples) if e is not None]
        self.dropped = len(examples) - len(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, i: int) -> dict[str, torch.Tensor]:
        return self.items[i]


def collate(pad_id: int):
    def fn(batch: list[dict[str, torch.Tensor]]) -> dict[str, torch.Tensor]:
        n = max(len(b["input_ids"]) for b in batch)
        ids = torch.full((len(batch), n), pad_id, dtype=torch.long)
        labels = torch.full((len(batch), n), -100, dtype=torch.long)
        attn = torch.zeros((len(batch), n), dtype=torch.long)
        for i, b in enumerate(batch):
            k = len(b["input_ids"])
            ids[i, :k] = b["input_ids"]
            labels[i, :k] = b["labels"]
            attn[i, :k] = 1
        return {"input_ids": ids, "labels": labels, "attention_mask": attn}

    return fn


@torch.no_grad()
def evaluate_loss(model, loader: DataLoader, device: torch.device, autocast) -> float:
    model.eval()
    total, count = 0.0, 0
    for batch in loader:
        batch = {k: v.to(device) for k, v in batch.items()}
        with autocast():
            out = model(**batch)
        n = int((batch["labels"] != -100).sum())
        total += float(out.loss) * n
        count += n
    model.train()
    return total / max(count, 1)


def train(cfg, out_dir: Path) -> dict[str, Any]:
    set_seed(int(cfg.seed))
    device = resolve_device(cfg.get("device"))
    tok = load_tokenizer(cfg.model.name)
    model = attach_lora(load_base(cfg, device), cfg)
    model.print_trainable_parameters()
    train_ex = read_examples(cfg.sft.train_path, cfg.sft.get("max_train_examples"))
    val_ex = read_examples(cfg.sft.val_path, cfg.sft.get("max_val_examples")) if cfg.sft.get("val_path") else []
    train_ds = SftDataset(tok, train_ex, int(cfg.sft.max_length))
    val_ds = SftDataset(tok, val_ex, int(cfg.sft.max_length))
    log_event(event="data", train=len(train_ds), val=len(val_ds), dropped=train_ds.dropped + val_ds.dropped)
    cf = collate(tok.pad_token_id)
    train_loader = DataLoader(train_ds, batch_size=int(cfg.sft.batch_size), shuffle=True, collate_fn=cf)
    val_loader = DataLoader(val_ds, batch_size=int(cfg.sft.batch_size), collate_fn=cf) if len(val_ds) else None
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=float(cfg.sft.lr), weight_decay=float(cfg.sft.weight_decay))
    steps_per_epoch = max(1, math.ceil(len(train_ds) / int(cfg.sft.batch_size)))
    total_steps = steps_per_epoch * int(cfg.sft.epochs)
    if cfg.sft.get("max_steps"):
        total_steps = min(total_steps, int(cfg.sft.max_steps))
    warmup = int(cfg.sft.get("warmup_steps", 0))

    def lr_at(step: int) -> float:
        if step < warmup:
            return float(cfg.sft.lr) * (step + 1) / warmup
        progress = (step - warmup) / max(1, total_steps - warmup)
        return float(cfg.sft.lr) * 0.5 * (1 + math.cos(math.pi * min(1.0, progress)))

    autocast_dtype = cfg.train.get("autocast_dtype")
    autocast = (lambda: torch.autocast("cuda", dtype=getattr(torch, autocast_dtype))) if (device.type == "cuda" and autocast_dtype) else (lambda: torch.autocast("cpu", enabled=False))
    model.train()
    step, t0 = 0, time.time()
    history: list[dict[str, Any]] = []
    done = False
    while not done:
        for batch in train_loader:
            for g in opt.param_groups:
                g["lr"] = lr_at(step)
            batch = {k: v.to(device) for k, v in batch.items()}
            with autocast():
                loss = model(**batch).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, float(cfg.sft.grad_clip))
            opt.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            if step % int(cfg.sft.log_interval) == 0 or step == total_steps:
                log_event(event="train", step=step, loss=round(float(loss), 4), lr=lr_at(step), elapsed_s=round(time.time() - t0, 1))
            if val_loader is not None and (step % int(cfg.sft.eval_every) == 0 or step == total_steps):
                vl = evaluate_loss(model, val_loader, device, autocast)
                history.append({"step": step, "val_loss": vl})
                log_event(event="eval", step=step, val_loss=round(vl, 4))
            if step % int(cfg.sft.save_every) == 0 and step < total_steps:
                save_adapter(model, out_dir / f"step_{step}", tok)
            if step >= total_steps:
                done = True
                break
    save_adapter(model, out_dir / "final", tok)
    summary = {"steps": step, "train_examples": len(train_ds), "val_history": history, "out": str(out_dir / "final")}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    log_event(event="done", **summary)
    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--override", nargs="*", default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args()
    cfg = load_config(args.config, args.override)
    out_dir = Path(args.out or cfg.sft.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    train(cfg, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
