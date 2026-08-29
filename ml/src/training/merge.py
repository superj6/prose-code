"""Merge a LoRA adapter into the base model for serving."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import torch
from transformers import AutoModelForCausalLM

from ml.src.models.loader import (
    load_adapter,
    load_tokenizer,
    merge_and_save,
)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True)
    p.add_argument("--adapter", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    tok = load_tokenizer(args.base)
    model = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=torch.bfloat16)
    merge_and_save(load_adapter(model, args.adapter), args.out, tok)
    print(f"merged -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
