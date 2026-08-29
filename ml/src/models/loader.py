"""Base model + tokenizer loading and LoRA attachment (peft). One checkpoint format: an adapter dir."""
from __future__ import annotations

from pathlib import Path

import torch
from omegaconf import DictConfig
from transformers import AutoModelForCausalLM, AutoTokenizer, PreTrainedModel


def load_tokenizer(name: str):
    tok = AutoTokenizer.from_pretrained(name, use_fast=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "right"
    return tok


def load_base(cfg: DictConfig, device: torch.device) -> PreTrainedModel:
    dtype_name = str(cfg.model.dtype)
    if device.type == "cpu" and dtype_name in ("bfloat16", "float16"):
        dtype_name = "float32"
    model = AutoModelForCausalLM.from_pretrained(cfg.model.name, torch_dtype=getattr(torch, dtype_name))
    model.to(device)
    return model


def attach_lora(model: PreTrainedModel, cfg: DictConfig):
    from peft import LoraConfig, get_peft_model

    lcfg = LoraConfig(
        r=int(cfg.lora.r), lora_alpha=int(cfg.lora.alpha), lora_dropout=float(cfg.lora.dropout),
        target_modules=list(cfg.lora.target_modules), task_type="CAUSAL_LM",
    )
    peft_model = get_peft_model(model, lcfg)
    return peft_model


def load_adapter(model: PreTrainedModel, adapter_dir: str | Path):
    from peft import PeftModel

    return PeftModel.from_pretrained(model, str(adapter_dir))


def save_adapter(peft_model, out_dir: str | Path, tokenizer=None) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    peft_model.save_pretrained(str(out))
    if tokenizer is not None:
        tokenizer.save_pretrained(str(out))


def merge_and_save(peft_model, out_dir: str | Path, tokenizer) -> None:
    """Merge LoRA into the base weights for serving (vLLM / llama.cpp export)."""
    merged = peft_model.merge_and_unload()
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(str(out_dir), safe_serialization=True)
    tokenizer.save_pretrained(str(out_dir))
