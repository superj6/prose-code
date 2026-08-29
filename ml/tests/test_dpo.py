import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sync" / "src"))
sys.path.insert(0, str(REPO))
pytest.importorskip("torch")

from ml.src.training.common import load_config
from ml.src.training.dpo import train as dpo_train
from ml.src.training.make_pairs import score_completion
from ml.tests.test_sft import _examples

CODE = "import os\n\n\ndef f(a):\n    return a\n\n\ndef g(b):\n    return b\n"
PROSE = "Import os.\n\n## f\nReturn a.\n\n## g\nReturn b.\n"
REC = {"id": "r", "language": "python", "prose": PROSE, "code": CODE, "changed_side": "code",
       "code_now": CODE.replace("    return b\n", "    return b * 2\n"),
       "blocks": [{"id": "b1", "prose": [0, 2], "code": [0, 3]}, {"id": "b2", "prose": [2, 5], "code": [3, 7]}, {"id": "b3", "prose": [5, 7], "code": [7, 9]}],
       "prose_after": PROSE.replace("Return b.", "Return twice b."), "code_after": CODE.replace("    return b\n", "    return b * 2\n"), "target_edits": []}


def test_score_completion_prefers_correct_edits():
    cfg = load_config()
    good = json.dumps({"edits": [{"op": "replace", "block": "b3", "text": "## g\nReturn twice b.", "reason": ""}]})
    wrong_block = json.dumps({"edits": [{"op": "replace", "block": "b1", "text": "hacked", "reason": ""}]})
    garbage = "not json"
    assert score_completion(REC, good, cfg) > score_completion(REC, wrong_block, cfg)
    assert score_completion(REC, garbage, cfg) == 0.0
    assert score_completion(REC, good, cfg) > 0.9


def test_dpo_two_steps(tiny_model_dir, tmp_path):
    pairs = tmp_path / "pairs.jsonl"
    pairs.write_text("".join(json.dumps({"messages": e["messages"], "chosen": e["completion"], "rejected": "{\"edits\": []}"}) + "\n" for e in _examples(3)))
    cfg = load_config(str(REPO / "configs" / "local_smoke.yaml"), overrides=[f"model.name={tiny_model_dir}", "lora.target_modules=[q_proj,v_proj]", "dpo.max_steps=2", "dpo.max_pairs=2"])
    summary = dpo_train(cfg, None, str(pairs), tmp_path / "out")
    assert summary["steps"] == 2 and summary["pairs"] == 2 and (tmp_path / "out" / "final" / "adapter_config.json").exists()
