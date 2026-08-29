import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sync" / "src"))
sys.path.insert(0, str(REPO))

from prosesync.config import load_config

from ml.src.data.dataset import render, stats
from ml.src.data.interactions_export import export

CODE = "import os\n\n\ndef f(a):\n    return a\n\n\ndef g(b):\n    return b\n"
PROSE = "Import os.\n\n## f\nReturn a.\n\n## g\nReturn b.\n"
BLOCKS = [{"id": "b1", "prose": [0, 2], "code": [0, 3]}, {"id": "b2", "prose": [2, 5], "code": [3, 7]}, {"id": "b3", "prose": [5, 7], "code": [7, 9]}]


def _sync_row(**over):
    code_now = CODE.replace("    return b\n", "    return b * 2\n")
    row = {
        "kind": "sync", "sync_id": "s1", "pair_id": "p", "language": "python", "prompt_version": "v1", "model": "gpt-x",
        "changed_side": "code", "other_side_dirty": False, "prose_before": PROSE, "code_before": code_now,
        "blocks_before": BLOCKS, "hunks": [{"old_start": 8, "old_lines": 1, "new_start": 8, "new_lines": 1, "old_text": "    return b\n", "new_text": "    return b * 2\n"}],
        "other_hunks": [], "affected": ["b3"], "editable": ["b2", "b3"], "raw": "{}",
        "edits_applied": [{"op": "replace", "block": "b3", "text": "## g\nReturn twice b.", "reason": "x"}],
        "line_edits": [], "prose_after": PROSE.replace("Return b.", "Return twice b."), "code_after": code_now,
        "blocks_after": BLOCKS, "warnings": [], "latency_ms": 5, "usage": {},
    }
    row.update(over)
    return row


def test_export_rebuilds_snapshot_and_joins_feedback():
    rows = [_sync_row(), {"kind": "feedback", "sync_id": "s1", "outcome": "accepted"}, _sync_row(model="mock", sync_id="s2"), _sync_row(sync_id="s3")]
    recs, st = export(rows)
    assert st["kept"] == 1 and st["dropped_mock"] == 1 and st["dup"] == 1
    r = recs[0]
    assert r["code"] == CODE and r["code_now"].endswith("return b * 2\n") and r["prose"] == PROSE
    assert r["meta"]["outcome"] == "accepted" and r["target_edits"][0]["block"] == "b3"
    assert [b["id"] for b in r["blocks"]] == ["b1", "b2", "b3"]


def test_render_uses_production_prompt():
    recs, _ = export([_sync_row()])
    ex = render(recs[0], load_config())
    assert ex["messages"][0]["role"] == "system"
    u = ex["messages"][1]["content"]
    assert "=== CODE ===" in u and "Affected blocks: b3." in u and "return b * 2" in u
    assert json.loads(ex["completion"])["edits"][0]["block"] == "b3"
    assert stats(recs)["lang:python"] == 1


def test_perturb_pipeline_with_mock(tmp_path):
    seed = tmp_path / "seed"
    (seed / "files").mkdir(parents=True)
    (seed / "files" / "a.py").write_text(CODE)
    (seed / "manifest.jsonl").write_text(json.dumps({"id": "a", "file": str(seed / "files" / "a.py"), "language": "python", "lines": 9}) + "\n")
    out = tmp_path / "synth.jsonl"
    proc = subprocess.run(
        [sys.executable, str(REPO / "ml" / "src" / "data" / "perturb.py"), "--manifest", str(seed / "manifest.jsonl"), "--out", str(out), "--backend", "mock", "--per-file", "2"],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(proc.stdout.strip().splitlines()[-1])["written"] == 2
    recs = [json.loads(l) for l in out.read_text().splitlines()]
    assert {r["changed_side"] for r in recs} == {"code", "prose"}
    assert all(r["source"] == "synthetic" and r["target_edits"] for r in recs)
