import json

from conftest import PY_CODE
from fastapi.testclient import TestClient

from prosesync.config import load_config
from prosesync.server import create_app


def _events(text):
    out = []
    for chunk in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_generate_sync_feedback_roundtrip(tmp_path):
    cfg = load_config(overrides=[f"log.dir={tmp_path}/logs"])
    client = TestClient(create_app(cfg, "mock"))
    assert client.get("/health").json()["backend"] == "mock"
    gen = client.post("/generate", json={"code": PY_CODE, "language": "python", "code_path": "x.py"}).json()
    code = PY_CODE.replace("DEFAULT_RETRIES = 3", "DEFAULT_RETRIES = 5")
    req = {
        "request_id": "r1",
        "pair": {"pair_id": "p", "language": "python", "code_path": "x.py", "prose": gen["prose"], "code": code,
                 "prose_version": 4, "code_version": 9},
        "base": {"prose": gen["prose"], "code": PY_CODE, "blocks": gen["blocks"]},
        "change": {"side": "code"},
    }
    with client.stream("POST", "/sync", json=req) as r:
        events = _events(r.read().decode())
    kinds = [k for k, _ in events]
    assert kinds[0] == "preview" and kinds[-2:] == ["edit", "done"]
    assert events[-2][1]["block"] == "p1" and events[-2][1]["side"] == "prose"  # the paragraph annotated `## b1`
    assert events[-1][1]["base_code_version"] == 9 and events[-1][1]["code"] == code
    assert client.post("/feedback", json={"sync_id": "r1", "outcome": "accepted", "dwell_s": 30}).json() == {"ok": True}


def test_sync_error_is_reported_as_event(tmp_path):
    cfg = load_config(overrides=[f"log.dir={tmp_path}/logs"])
    client = TestClient(create_app(cfg, "mock"))
    req = {
        "request_id": "r1",
        "pair": {"pair_id": "p", "language": "python", "code_path": "x.py", "prose": "a\n\nb\n", "code": "x = 1\n"},
        "base": {"prose": "a\n", "code": "x = 1\n", "blocks": [{"id": "b1", "prose": [0, 1], "code": [0, 1]}]},
        "change": {"side": "prose"},
    }
    with client.stream("POST", "/sync", json=req) as r:
        events = _events(r.read().decode())
    assert events[-1][0] in ("done", "error")


def test_align_rebuilds_or_409(tmp_path):
    cfg = load_config(overrides=[f"log.dir={tmp_path}/logs", "sync.file_mode=paired"])
    client = TestClient(create_app(cfg, "mock"))
    gen = client.post("/generate", json={"code": PY_CODE, "language": "python", "code_path": "x.py"}).json()
    r = client.post("/align", json={"prose": gen["prose"], "code": PY_CODE, "language": "python"})
    assert r.status_code == 200 and [b["id"] for b in r.json()["blocks"]] == ["s", "b1", "b2", "b3"]
    r = client.post("/align", json={"prose": "only one paragraph\n", "code": PY_CODE, "language": "python"})
    assert r.status_code == 409
    # free mode never needs pairing: independent partitions
    free = TestClient(create_app(load_config(overrides=[f"log.dir={tmp_path}/logs"]), "mock"))
    r = free.post("/align", json={"prose": "only one paragraph\n", "code": PY_CODE, "language": "python"}).json()
    assert [b["id"] for b in r["blocks"]] == ["p1"] and [b["id"] for b in r["code_blocks"]] == ["b1", "b2", "b3"]


def test_create_from_new_prose_file(tmp_path):
    cfg = load_config(overrides=[f"log.dir={tmp_path}/logs"])
    client = TestClient(create_app(cfg, "mock"))
    r = client.post("/create", json={"prose": "# c.py\nGreets people.\n", "language": "python", "code_path": "c.py"}).json()
    assert r["code"].strip() and r["prose"].startswith("# c.py\nGreets people.") and r["blocks"][0]["id"] == "s"


def test_align_prefers_committed_base(tmp_path):
    import subprocess

    cfg = load_config(overrides=[f"log.dir={tmp_path}/logs"])
    client = TestClient(create_app(cfg, "mock"))
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    run("init", "-q"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    gen = client.post("/generate", json={"code": PY_CODE, "language": "python", "code_path": "x.py"}).json()
    (repo / "x.py").write_text(PY_CODE); (repo / "x.py.prose").write_text(gen["prose"])
    run("add", "."); run("commit", "-q", "-m", "pair")
    edited = PY_CODE + "\n\ndef extra():\n    return 1\n"   # a clone with an uncommitted code edit
    (repo / "x.py").write_text(edited)
    r = client.post("/align", json={"prose": gen["prose"], "code": edited, "language": "python", "code_path": str(repo / "x.py"), "prose_path": str(repo / "x.py.prose")}).json()
    assert r["source"] == "git" and r["code"] == PY_CODE and [b["id"] for b in r["blocks"]] == ["s", "p1", "p2", "p3"]
    assert [b["id"] for b in r["code_blocks"]] == ["b1", "b2", "b3"]
