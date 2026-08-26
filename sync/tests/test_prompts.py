from conftest import PY_CODE, PY_PROSE

from prosesync import blocks as B
from prosesync.models import Hunk
from prosesync.prompts import build_generate_messages, build_sync_messages


def test_sync_prompt_marks_affected_and_direction():
    blocks = B.make_blocks(B.segment_prose(PY_PROSE), B.segment_code(PY_CODE, "python"))
    msgs = build_sync_messages(
        language="python", prose=PY_PROSE, code=PY_CODE, blocks=blocks, changed="code",
        hunks=[Hunk(old_start=7, old_lines=1, new_start=7, new_lines=1, old_text="x\n", new_text="y\n")],
        affected=["b2"], editable=["b1", "b2", "b3"],
    )
    assert msgs[0]["role"] == "system" and "block-level edits" in msgs[0]["content"]
    u = msgs[1]["content"]
    assert "[b1]" in u and "[b2]" in u and "AFFECTED" not in u and "Produce edits to the PROSE side" in u
    assert "Affected blocks: b2. Editable blocks: b1, b2, b3." in u
    assert "@@ -8,1 +8,1 @@\n-x\n+y" in u
    # cache layout: documents precede everything request-specific
    assert u.index("=== CODE ===") < u.index("=== PROSE ===") < u.index("=== CHANGE") < u.index("Affected blocks")


def test_generate_prompt_lists_blocks():
    blocks = B.make_blocks([(0, 1)] * 3, B.segment_code(PY_CODE, "python"))
    u = build_generate_messages(language="python", code=PY_CODE, blocks=blocks)[1]["content"]
    assert "[b1]" in u and "[b3]" in u and "3 blocks" in u


def test_windowed_rendering_collapses_far_blocks():
    from prosesync.prompts import render_blocks, window_ids

    code = "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(20))
    ranges = B.segment_code(code, "python")
    blocks = B.make_blocks([(i, i + 1) for i in range(len(ranges))], ranges)
    assert len(blocks) == 20
    assert window_ids(blocks, ["b10"], max_full_blocks=14, radius=2) == ["b1", "b8", "b9", "b10", "b11", "b12"]
    assert window_ids(blocks[:5], ["b3"], max_full_blocks=14, radius=2) is None
    rendered = render_blocks(code, blocks, "code", ["b1", "b8", "b9", "b10", "b11", "b12"])
    assert "[b10]\ndef f9():" in rendered and "[b5] (collapsed: def f4():)" in rendered
    assert rendered.count("(collapsed:") == 14
