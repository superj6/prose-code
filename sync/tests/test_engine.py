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
    assert len(gen.blocks) == 3
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
    assert "(updated)" in resp.prose.split("\n\n")[2] and "(updated)" not in resp.prose.split("\n\n")[0]
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
    prose = gen.prose.replace("Describes: `class Cache:`", "Describes: `class Cache:` and more")
    req = SyncRequest(
        request_id="r2",
        pair=Pair(pair_id="p", language="python", code_path="x.py", prose=prose, code=PY_CODE),
        base=Snapshot(prose=gen.prose, code=PY_CODE, blocks=gen.blocks), change=Change(side="prose"),
    )
    resp = asyncio.run(engine.sync(req))
    assert resp.target_side == "code"
    assert any("b1 rejected" in w for w in resp.warnings) and any("zz rejected" in w for w in resp.warnings)
    assert resp.code.split("\n\n")[0] == PY_CODE.split("\n\n")[0]  # b1 untouched


def test_sync_no_change_is_a_noop(engine):
    gen = asyncio.run(engine.generate(PY_CODE, "python", "x.py"))
    req = SyncRequest(
        request_id="r3", pair=Pair(pair_id="p", language="python", code_path="x.py", prose=gen.prose, code=PY_CODE),
        base=Snapshot(prose=gen.prose, code=PY_CODE, blocks=gen.blocks), change=Change(side="code"),
    )
    resp = asyncio.run(engine.sync(req))
    assert resp.line_edits == [] and resp.warnings == ["no change vs snapshot"]
