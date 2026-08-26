from conftest import PY_CODE, PY_PROSE

from prosesync import blocks as B
from prosesync.align import resegment
from prosesync.names import code_unit_names, names_conflict, prose_heading_names


def test_code_and_prose_names():
    cr = B.segment_code(PY_CODE, "python")
    assert code_unit_names(PY_CODE, "python", cr) == [None, "fetch", "cache"]
    assert prose_heading_names(PY_PROSE, B.segment_prose(PY_PROSE)) == [None, "fetch", "cache"]
    ts = "import x from 'x';\n\nexport function a() {}\n\nexport class B {}\n\nexport const c = () => 2;\n"
    assert code_unit_names(ts, "typescript", B.segment_code(ts, "typescript")) == [None, "a", "b", "c"]
    go = "package main\n\nfunc main() {}\n\ntype T struct{}\n"
    assert code_unit_names(go, "go", B.segment_code(go, "go")) == [None, "main", "t"]
    assert prose_heading_names("## `pkg.wordCount`\nx\n\n## class Foo(Base)\ny\n", [(0, 2), (2, 4)]) == ["wordcount", "foo"]


def test_names_conflict():
    assert names_conflict([None, "a", "b"], [None, "a", "b"]) is None
    assert names_conflict([None, "a", None], ["x", "a", "b"]) is None  # None never conflicts
    assert names_conflict(["a", "b"], ["a", "c"]) == 1


def test_resegment_pairs_by_order_and_validates_names():
    assert [b.id for b in resegment(PY_PROSE, PY_CODE, "python")] == ["b1", "b2", "b3"]
    prose2 = PY_PROSE + "\n## helper\nReturn 1.\n"
    code2 = PY_CODE + "\n\ndef helper():\n    return 1\n"
    assert [b.id for b in resegment(prose2, code2, "python")] == ["b1", "b2", "b3", "b4"]
    # counts differ -> unrecoverable
    assert resegment(PY_PROSE.split("\n\n", 1)[1], PY_CODE, "python") is None
    # counts equal but the headings name different symbols than the code -> stale prose
    swapped = PY_PROSE.replace("## fetch", "## TEMP").replace("## class Cache", "## fetch").replace("## TEMP", "## class Cache")
    assert resegment(swapped, PY_CODE, "python") is None
