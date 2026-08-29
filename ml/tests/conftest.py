import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO / "sync" / "src", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

pytest.importorskip("torch")


@pytest.fixture(scope="session")
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
