import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ml.src.data.git_mine import mine

CODE = "import os\n\n\ndef f(a):\n    return a\n\n\ndef g(b):\n    return b\n" + "\n\n\ndef h(c):\n    return c\n" * 6


def _repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (r / "m.py").write_text(CODE)
    run("add", "."); run("commit", "-q", "-m", "init")
    (r / "m.py").write_text(CODE.replace("    return b\n", "    return b * 2\n"))
    run("commit", "-q", "-am", "double g")
    (r / "m.py").write_text("x = 1\n" * 100)  # big rewrite: filtered out
    run("commit", "-q", "-am", "rewrite")
    return r


def test_mine_finds_small_single_file_commits(tmp_path):
    r = _repo(tmp_path)
    edits = mine(str(r), max_hunks=3, min_lines=5, max_lines=300, limit=10)
    assert [e["subject"] for e in edits] == ["double g"]
    e = edits[0]
    assert e["language"] == "python" and e["hunks"] == 1 and "return b * 2" in e["code_now"] and "return b\n" in e["code"]


def test_label_with_mock(tmp_path):
    r = _repo(tmp_path)
    edits = tmp_path / "edits.jsonl"
    edits.write_text("".join(json.dumps(e) + "\n" for e in mine(str(r), 3, 5, 300, 10)))
    out = tmp_path / "records.jsonl"
    proc = subprocess.run([sys.executable, str(REPO / "ml" / "src" / "data" / "git_mine.py"), "--label", str(edits), "--out", str(out), "--backend", "mock"], capture_output=True, text=True, check=True)
    assert json.loads(proc.stdout.strip().splitlines()[-1])["kept"] == 1
    rec = json.loads(out.read_text().splitlines()[0])
    assert rec["source"] == "git" and rec["changed_side"] == "code" and rec["target_edits"]
