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
    assert "[b2 AFFECTED]" in u and "[b1]" in u and "Produce edits to the PROSE side" in u
    assert "@@ -8,1 +8,1 @@\n-x\n+y" in u


def test_generate_prompt_lists_blocks():
    blocks = B.make_blocks([(0, 1)] * 3, B.segment_code(PY_CODE, "python"))
    u = build_generate_messages(language="python", code=PY_CODE, blocks=blocks)[1]["content"]
    assert "[b1]" in u and "[b3]" in u and "3 blocks" in u
