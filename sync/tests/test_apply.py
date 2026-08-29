from conftest import PY_CODE, PY_PROSE

from prosesync import blocks as B
from prosesync.align import realign
from prosesync.apply import DocState, apply_edits
from prosesync.models import Edit, Snapshot


def _blocks():
    return B.make_blocks(B.segment_prose(PY_PROSE), B.segment_code(PY_CODE, "python"))


def test_replace_prose_block_keeps_rest_verbatim():
    blocks = _blocks()
    edits = [Edit(op="replace", block="b2", text="## fetch\nNew description.", reason="x")]
    prose, code, new_blocks, line_edits = apply_edits(PY_PROSE, PY_CODE, blocks, edits, "prose", "python")
    assert code == PY_CODE
    assert prose.split("\n\n")[1] == "## fetch\nNew description."
    assert prose.split("\n\n")[0] == PY_PROSE.split("\n\n")[0]
    assert len(line_edits) == 1 and line_edits[0].start == 2 and line_edits[0].end == 6
    assert B.check_partition(new_blocks, "prose", len(B.split_lines(prose))) is None
    assert B.check_partition(new_blocks, "code", len(B.split_lines(code))) is None


def test_replace_code_block_preserves_trailing_blank_lines():
    blocks = _blocks()
    new_fn = "def fetch(url):\n    return _get(url)"
    _, code, new_blocks, _ = apply_edits(PY_PROSE, PY_CODE, blocks, [Edit(op="replace", block="b2", text=new_fn)], "code", "python")
    assert "def fetch(url):\n    return _get(url)\n\n\nclass Cache" in code
    assert [b.id for b in new_blocks] == ["b1", "b2", "b3"]


def test_delete_block_merges_leftover_into_previous():
    blocks = _blocks()
    prose, code, new_blocks, _line_edits = apply_edits(PY_PROSE, PY_CODE, blocks, [Edit(op="delete", block="b2")], "prose", "python")
    assert [b.id for b in new_blocks] == ["b1", "b3"]
    assert code == PY_CODE  # source side untouched
    assert new_blocks[0].code == (0, 15)  # b2's code lines now belong to b1
    assert "## fetch" not in prose


def test_replace_that_splits_into_two_blocks_on_both_sides():
    blocks = _blocks()
    # user added a second function inside b2's code region; model rewrites b2's prose as 2 paragraphs
    code = PY_CODE.replace("class Cache:", "def helper():\n    return 1\n\n\nclass Cache:")
    base = Snapshot(prose=PY_PROSE, code=PY_CODE, blocks=blocks)
    shifted, _hunks, _, _ = realign(base, PY_PROSE, code, "python", "code", False)
    assert [b.id for b in shifted] == ["b1", "b2", "b3"]
    state = DocState(PY_PROSE, code, shifted, "python")
    state.apply(Edit(op="replace", block="b2", text="## fetch\nFetch stuff.\n\n## helper\nReturn 1."), "prose")
    assert state.ids == ["b1", "b2", "b4", "b3"]
    assert state.code_parts[2].startswith("def helper")
    assert state.prose_parts[2].startswith("## helper")
    nb = state.blocks()
    assert B.check_partition(nb, "prose", len(B.split_lines(state.text("prose")))) is None
    assert B.check_partition(nb, "code", len(B.split_lines(state.text("code")))) is None


def test_realign_with_both_sides_dirty():
    blocks = _blocks()
    base = Snapshot(prose=PY_PROSE, code=PY_CODE, blocks=blocks)
    code = PY_CODE.replace("return self.d.get(k)", "return self.d.get(k, None)")
    prose = PY_PROSE.replace("Import `os`", "Import `os` (needed)")
    shifted, hunks, other, _ = realign(base, prose, code, "python", "code", True)
    assert len(hunks) == 1 and len(other) == 1
    assert [b.id for b in shifted] == ["b1", "b2", "b3"]


def test_realign_rebuilds_inconsistent_map():
    blocks = _blocks()
    broken = [blocks[0].model_copy(update={"code": (0, 2)})] + blocks[1:]
    base = Snapshot(prose=PY_PROSE, code=PY_CODE, blocks=broken)
    shifted, _, _, _ = realign(base, PY_PROSE, PY_CODE, "python", "code", False)
    assert [b.code for b in shifted] == [b.code for b in blocks]
