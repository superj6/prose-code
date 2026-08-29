import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sync" / "src"))
sys.path.insert(0, str(REPO))

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

from ml.src.training.common import load_config
from ml.src.training.sft import SftDataset, encode_example, train


@pytest.fixture(scope="module")
def tiny_model_dir(tmp_path_factory):
    """A random 2-layer Qwen2 with a tiny vocab and a minimal tokenizer (no downloads)."""
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast, Qwen2Config, Qwen2ForCausalLM

    d = tmp_path_factory.mktemp("tiny")
    vocab = {tok: i for i, tok in enumerate(["<pad>", "<eos>", "<unk>"] + [chr(c) for c in range(32, 127)])}
    raw = Tokenizer(models.WordLevel(vocab=vocab, unk_token="<unk>"))
    raw.pre_tokenizer = pre_tokenizers.Split("", "isolated")
    tok = PreTrainedTokenizerFast(tokenizer_object=raw, pad_token="<pad>", eos_token="<eos>", unk_token="<unk>")
    tok.save_pretrained(str(d))
    cfg = Qwen2Config(vocab_size=len(vocab), hidden_size=32, intermediate_size=64, num_hidden_layers=2, num_attention_heads=2, num_key_value_heads=1, max_position_embeddings=512)
    Qwen2ForCausalLM(cfg).save_pretrained(str(d))
    return d


def _examples(n=3):
    return [{"messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": f"user {i} " * 5}], "completion": json.dumps({"edits": [{"op": "replace", "block": "b1", "text": "x", "reason": "r"}]})} for i in range(n)]


def test_encode_masks_prompt_tokens(tiny_model_dir):
    from ml.src.models.loader import load_tokenizer

    tok = load_tokenizer(str(tiny_model_dir))
    enc = encode_example(tok, _examples(1)[0], max_length=512)
    n_prompt = int((enc["labels"] == -100).sum())
    assert n_prompt > 0 and enc["labels"][-1] == tok.eos_token_id and len(enc["input_ids"]) == len(enc["labels"])
    ds = SftDataset(tok, _examples(3), max_length=64)  # too short for prompt+completion -> dropped
    assert ds.dropped >= 0


def test_train_two_steps_saves_adapter(tiny_model_dir, tmp_path):
    train_path, val_path = tmp_path / "train.jsonl", tmp_path / "val.jsonl"
    train_path.write_text("".join(json.dumps(e) + "\n" for e in _examples(4)))
    val_path.write_text("".join(json.dumps(e) + "\n" for e in _examples(2)))
    cfg = load_config(str(REPO / "configs" / "local_smoke.yaml"), overrides=[
        f"model.name={tiny_model_dir}", f"sft.train_path={train_path}", f"sft.val_path={val_path}",
        "sft.max_steps=2", "sft.batch_size=2", "sft.max_train_examples=4", "sft.max_val_examples=2", "lora.target_modules=[q_proj,v_proj]",
    ])
    summary = train(cfg, tmp_path / "out")
    assert summary["steps"] == 2 and (tmp_path / "out" / "final" / "adapter_config.json").exists()
    assert summary["val_history"] and summary["val_history"][-1]["val_loss"] > 0
