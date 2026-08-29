import asyncio

import pytest
from conftest import PY_CODE

from prosesync import blocks as B
from prosesync.backends.mock_backend import MockBackend
from prosesync.config import load_config
from prosesync.engine import Engine, pair_id_for
from prosesync.models import Change, Pair, Snapshot, SyncRequest


@pytest.fixture
def engine(tmp_path):
    cfg = load_config(overrides=[f"log.dir={tmp_path}/logs"])
    return Engine(cfg, MockBackend())


def test_generate_then_sync_code_change(engine, tmp_path):
    gen = asyncio.run(engine.generate(PY_CODE, "python", "x.py"))
    assert [b.id for b in gen.blocks] == ["s", "b1", "b2", "b3"]
    assert gen.prose.startswith("# x.py\nMock summary of the file.\n\n")
    assert B.check_partition(gen.blocks, "prose", len(B.split_lines(gen.prose))) is None
    code = PY_CODE.replace("return self.d.get(k)", "return self.d.get(k, None)")
    req = SyncRequest(
        request_id="r1",
        pair=Pair(pair_id=pair_id_for("x.py"), language="python", code_path="x.py", prose=gen.prose, code=code),
        base=Snapshot(prose=gen.prose, code=PY_CODE, blocks=gen.blocks), change=Change(side="code"),
    )
    seen = []

    async def cb(le):
        seen.append(le)

    resp = asyncio.run(engine.sync(req, on_line_edit=cb))
    assert resp.target_side == "prose" and resp.code == code
    assert [le.block for le in seen] == ["b3"] and resp.line_edits == seen
    assert "(updated)" in resp.prose.split("\n\n")[3] and "(updated)" not in resp.prose.split("\n\n")[1]
    assert resp.prose.startswith("# x.py\n")  # summary block untouched by the mock
    assert resp.warnings == []
    logs = list((tmp_path / "logs").glob("*.jsonl"))
    assert logs and sum(1 for _ in logs[0].open()) == 2


def test_sync_rejects_edits_outside_editable_set(engine):
    gen = asyncio.run(engine.generate(PY_CODE, "python", "x.py"))

    class Rogue(MockBackend):
        async def complete_json(self, messages, schema, schema_name, on_object=None, model=None, **kw):
            await on_object({"op": "replace", "block": "b1", "text": "hacked", "reason": "x"})
            await on_object({"op": "replace", "block": "zz", "text": "hacked", "reason": "x"})
            return await super().complete_json(messages, schema, schema_name, None, model, **kw)

    engine.backend = Rogue()
    prose = gen.prose.replace("describes `class Cache:`", "describes `class Cache:` and more")
    req = SyncRequest(
        request_id="r2",
        pair=Pair(pair_id="p", language="python", code_path="x.py", prose=prose, code=PY_CODE),
        base=Snapshot(prose=gen.prose, code=PY_CODE, blocks=gen.blocks), change=Change(side="prose"),
    )
    resp = asyncio.run(engine.sync(req))
    assert resp.target_side == "code"
    assert any("b1 rejected" in w for w in resp.warnings) and any("zz rejected" in w for w in resp.warnings)
    assert "s" not in engine.backend.calls[-1]["messages"][-1]["content"].split("Editable blocks:")[1].split(".")[0]
    assert resp.code.split("\n\n")[0] == PY_CODE.split("\n\n")[0]  # b1 untouched


def test_sync_no_change_is_a_noop(engine):
    gen = asyncio.run(engine.generate(PY_CODE, "python", "x.py"))
    req = SyncRequest(
        request_id="r3", pair=Pair(pair_id="p", language="python", code_path="x.py", prose=gen.prose, code=PY_CODE),
        base=Snapshot(prose=gen.prose, code=PY_CODE, blocks=gen.blocks), change=Change(side="code"),
    )
    resp = asyncio.run(engine.sync(req))
    assert resp.line_edits == [] and resp.warnings == ["no change vs snapshot"]


def test_verification_repair_round(tmp_path):
    cfg = load_config(overrides=[f"log.dir={tmp_path}/logs", "verify.enabled=true", "verify.repair_rounds=1"])
    calls = []

    class Flaky(MockBackend):
        async def complete_json(self, messages, schema, schema_name, on_object=None, model=None, **kw):
            if schema_name != "edits":
                return await super().complete_json(messages, schema, schema_name, on_object, model, **kw)
            calls.append(messages)
            text = "def get(self, k):\n    return self.d.get(k, None\n" if len(calls) == 1 else "def get(self, k):\n    return self.d.get(k, None)\n"
            await on_object({"op": "replace", "block": "b3", "text": "class Cache:\n    def __init__(self):\n        self.d = {}\n\n    " + text.replace("\n", "\n    ").rstrip() + "\n", "reason": "x"})
            return await super().complete_json(messages, schema, schema_name, None, model, **kw)

    engine = Engine(cfg, Flaky())
    gen = asyncio.run(engine.generate(PY_CODE, "python", "x.py"))
    prose = gen.prose.replace("describes `class Cache:`", "describes `class Cache:` returning None when missing")
    req = SyncRequest(
        request_id="r4", pair=Pair(pair_id="p", language="python", code_path="x.py", prose=prose, code=PY_CODE),
        base=Snapshot(prose=gen.prose, code=PY_CODE, blocks=gen.blocks), change=Change(side="prose"),
    )
    resp = asyncio.run(engine.sync(req))
    assert len(calls) == 2 and "fails verification" in calls[1][-1]["content"]
    assert resp.verification is not None and resp.verification.ok
    assert resp.code.endswith("return self.d.get(k, None)\n")
    assert resp.line_edits[-1].block == "*" and resp.line_edits[-1].start == 0
    assert any(w.startswith("repair round 1: ok") for w in resp.warnings)


def test_both_dirty_runs_two_passes_and_keeps_user_text(engine):
    gen = asyncio.run(engine.generate(PY_CODE, "python", "x.py"))
    code = PY_CODE.replace("return self.d.get(k)", "return self.d.get(k, None)")  # user edit on code (b3)
    prose = gen.prose.replace("Block b1: describes `import os`", "Block b1: describes `import os` (USER NOTE)")  # user edit on prose (b1)
    req = SyncRequest(
        request_id="r5", pair=Pair(pair_id="p", language="python", code_path="x.py", prose=prose, code=code),
        base=Snapshot(prose=gen.prose, code=PY_CODE, blocks=gen.blocks), change=Change(side="code"), other_side_dirty=True,
    )
    resp = asyncio.run(engine.sync(req))
    sides = [(le.side, le.block) for le in resp.line_edits]
    assert ("prose", "b3") in sides and ("code", "b1") in sides  # pass 1 then pass 2
    assert "(USER NOTE)" in resp.prose  # the user's prose edit survived pass 1
    assert "return self.d.get(k, None)" in resp.code  # the user's code edit survived pass 2
    assert any(w.startswith("pass 2 (prose -> code)") for w in resp.warnings)
    assert not any("rejected" in w for w in resp.warnings)


def test_generate_code_from_prose(engine):
    prose = "# new.py\nA helper module.\n\nImport `os`.\n\n## greet\nReturn a greeting for `name`.\n"
    resp = asyncio.run(engine.generate_code(prose, "python", "new.py"))
    assert [b.id for b in resp.blocks] == ["s", "b1", "b2"]
    assert resp.code == "# Import `os`.\npass\n\n\n# ## greet\npass\n"
    assert B.check_partition(resp.blocks, "code", len(B.split_lines(resp.code))) is None
    assert B.check_partition(resp.blocks, "prose", len(B.split_lines(prose))) is None
    # the resulting pair round-trips through the normal sync path
    req = SyncRequest(
        request_id="r6", pair=Pair(pair_id="p", language="python", code_path="new.py", prose=prose.replace("## greet", "## greet_loudly"), code=resp.code),
        base=Snapshot(prose=prose, code=resp.code, blocks=resp.blocks), change=Change(side="prose"),
    )
    out = asyncio.run(engine.sync(req))
    assert out.line_edits and out.line_edits[0].block == "b2"


def test_create_from_summary_only_prose(engine):
    prose, code, blocks = asyncio.run(engine.create_from_prose("# c.py\nGreets people.\n", "python", "c.py"))
    assert code.startswith("# ") and code.strip()
    assert prose.startswith("# c.py\nGreets people.\n\n") and blocks[0].id == "s" and len(blocks) >= 2
    assert B.check_partition(blocks, "prose", len(B.split_lines(prose))) is None
    assert B.check_partition(blocks, "code", len(B.split_lines(code))) is None
