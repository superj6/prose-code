from conftest import PY_CODE, PY_PROSE

from prosesync import blocks as B
from prosesync.models import Block, Hunk


def test_segment_python_groups_small_units_and_keeps_definitions():
    ranges = B.segment_code(PY_CODE, "python")
    assert ranges[0] == (0, 6)  # imports + constant + trailing blanks
    assert len(ranges) == 3
    assert B.check_partition(B.make_blocks([(0, 1)] * 3, ranges), "code", len(B.split_lines(PY_CODE))) is None


def test_segment_prose_paragraphs():
    assert B.segment_prose(PY_PROSE) == [(0, 2), (2, 6), (6, 8)]


def test_segment_other_languages():
    ts = "import x from 'x';\n\nexport function a() {\n  return 1;\n}\n\nexport const b = () => 2;\n"
    assert len(B.segment_code(ts, "typescript")) == 3
    go = "package main\n\nimport \"fmt\"\n\nfunc main() {\n\tfmt.Println(1)\n}\n"
    assert len(B.segment_code(go, "go")) == 2
    unknown = "a\nb\n\nc\n"
    assert B.segment_code(unknown, "brainfudge") == [(0, 3), (3, 4)]


def test_partition_always_covers_file():
    for code, lang in [("\n\n\nx = 1\n", "python"), ("# only comment\n", "python"), ("", "python")]:
        ranges = B.segment_code(code, lang)
        n = len(B.split_lines(code))
        assert sum(e - s for s, e in ranges) == n
        assert not ranges or ranges[0][0] == 0


def test_hunks_and_shift_insert_inside_block():
    old = "a\nb\n\nc\nd\n"
    new = "a\nb\nb2\n\nc\nd\n"
    hunks = B.compute_hunks(old, new)
    assert [(h.old_start, h.old_lines, h.new_lines) for h in hunks] == [(2, 0, 1)]
    blocks = [Block(id="b1", prose=(0, 1), code=(0, 3)), Block(id="b2", prose=(1, 2), code=(3, 5))]
    shifted = B.shift_ranges(blocks, hunks, "code")
    assert [b.code for b in shifted] == [(0, 4), (4, 6)]
    assert B.affected_block_ids(blocks, hunks, "code") == ["b1"]


def test_shift_insert_at_boundary_attaches_to_previous_block():
    blocks = [Block(id="b1", prose=(0, 1), code=(0, 3)), Block(id="b2", prose=(1, 2), code=(3, 5))]
    hunks = [Hunk(old_start=3, old_lines=0, new_start=3, new_lines=2)]
    assert [b.code for b in B.shift_ranges(blocks, hunks, "code")] == [(0, 5), (5, 7)]
    assert B.affected_block_ids(blocks, hunks, "code") == ["b1"]


def test_shift_replacement_straddling_boundary_goes_to_earlier_block():
    blocks = [Block(id="b1", prose=(0, 1), code=(0, 3)), Block(id="b2", prose=(1, 2), code=(3, 6))]
    hunks = [Hunk(old_start=2, old_lines=2, new_start=2, new_lines=5)]
    shifted = B.shift_ranges(blocks, hunks, "code")
    assert [b.code for b in shifted] == [(0, 7), (7, 9)]
    assert B.affected_block_ids(blocks, hunks, "code") == ["b1", "b2"]
    assert B.affected_block_ids(blocks, hunks, "code", context=1) == ["b1", "b2"]


def test_shift_delete_whole_block_leaves_empty_range():
    blocks = [Block(id="b1", prose=(0, 1), code=(0, 3)), Block(id="b2", prose=(1, 2), code=(3, 6)), Block(id="b3", prose=(2, 3), code=(6, 8))]
    hunks = [Hunk(old_start=3, old_lines=3, new_start=3, new_lines=0)]
    assert [b.code for b in B.shift_ranges(blocks, hunks, "code")] == [(0, 3), (3, 3), (3, 5)]
