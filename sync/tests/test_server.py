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
    assert events[0][1]["block"] == "b1" and events[0][1]["start"] == 0
    assert events[-2][1]["block"] == "b1" and events[-2][1]["side"] == "prose"
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
