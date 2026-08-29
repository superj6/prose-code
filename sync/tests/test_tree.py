import asyncio

import pytest
from conftest import PY_CODE

from prosesync import store, tree
from prosesync.backends.mock_backend import MockBackend
from prosesync.config import load_config
from prosesync.engine import Engine

TS = "export function a() {\n  return 1;\n}\n\nexport const b = 2;\n"


@pytest.fixture
def engine(tmp_path):
    return Engine(load_config(overrides=[f"log.dir={tmp_path}/logs"]), MockBackend())


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "a.py").write_text(PY_CODE)
    (root / "sub" / "b.ts").write_text(TS)
    (root / "notes.txt").write_text("ignored\n")
    return root


def test_generate_tree_writes_file_and_dir_prose(engine, project):
    result = asyncio.run(tree.generate_tree(engine, project))
    names = sorted(p.relative_to(project).as_posix() for p in result.generated)
    assert names == ["DIR.prose", "a.py.prose", "sub/DIR.prose", "sub/b.ts.prose"] and not result.errors
    assert tree.summary_text((project / "a.py.prose").read_text()) == "Mock summary of the file."
    doc = tree.synthetic_doc(tree.children(project))
    assert doc.startswith("## a.py\nMock summary of the file.\n\n## sub/\n")
    root_prose = (project / "DIR.prose").read_text()
    assert root_prose.startswith("# proj/\nMock summary of the file.\n\n")
    snap = store.load_snapshot(tree.dir_code_path(project))
    assert snap is not None and [b.id for b in snap.blocks] == ["s", "b1", "b2"]
    # idempotent: nothing regenerated without --overwrite, DIR.prose refreshed
    again = asyncio.run(tree.generate_tree(engine, project))
    assert sorted(p.name for p in again.generated) == ["DIR.prose", "DIR.prose"]


def test_propagate_up_updates_ancestors(engine, project):
    asyncio.run(tree.generate_tree(engine, project))
    b = project / "sub" / "b.ts"
    prose_path = store.prose_path(b)
    prose_path.write_text(tree.replace_summary(prose_path.read_text(), "b.ts", "Now a totally different summary."))
    result = asyncio.run(tree.propagate_up(engine, b))
    assert [p.relative_to(project).as_posix() for p, _ in result.synced] == ["sub/DIR.prose"]  # the mock leaves sub's summary alone -> stop
    sub_prose = (project / "sub" / "DIR.prose").read_text()
    assert "(updated)" in sub_prose
    # a second propagate with nothing changed is a no-op
    assert asyncio.run(tree.propagate_up(engine, b)).synced == []


def test_propagate_down_pushes_summary_into_child(engine, project):
    asyncio.run(tree.generate_tree(engine, project))
    dir_prose = project / "DIR.prose"
    text = dir_prose.read_text()
    text = text.replace("Block b1: describes `## a.py`", "Block b1: describes `## a.py` (user wants more)")
    dir_prose.write_text(text)
    result = asyncio.run(tree.propagate_down(engine, project))
    synced = [p.relative_to(project).as_posix() for p, _ in result.synced]
    assert synced[0] == "DIR.prose" and "a.py" in synced and not result.errors
    # the child's summary now carries the pushed text and its code was re-synced (mock stub)
    child_prose = (project / "a.py.prose").read_text()
    assert child_prose.startswith("# a.py\n") and "(updated)" in tree.summary_text(child_prose)
    assert "# " in (project / "a.py").read_text()  # mock code edit landed
    assert "(updated)" in child_prose.split("\n\n")[1]  # ...and the paragraph for the changed block was refreshed


def test_replace_summary_roundtrip():
    prose = "# f.py\nOld.\n\n## g\nBody.\n"
    assert tree.replace_summary(prose, "f.py", "New one.") == "# f.py\nNew one.\n\n## g\nBody.\n"
    assert tree.replace_summary("## g\nBody.\n", "f.py", "Added.") == "# f.py\nAdded.\n\n## g\nBody.\n"
    assert tree.summary_text("no summary\n") is None


def test_propagate_up_with_dirty_dir_prose_keeps_real_doc_in_snapshot(engine, project):
    asyncio.run(tree.generate_tree(engine, project))
    dir_prose = project / "DIR.prose"
    # the user's unpushed edit is on the sub/ paragraph (b2); the child change hits a.py (b1)
    dir_prose.write_text(dir_prose.read_text().replace("Block b2: describes `## sub/`", "Block b2: describes `## sub/` (unpushed)"))
    a = project / "a.py"
    ap = store.prose_path(a)
    ap.write_text(tree.replace_summary(ap.read_text(), "a.py", "Changed summary."))
    result = asyncio.run(tree.propagate_up(engine, a))
    assert result.synced and any("unpushed" in e for _, e in result.errors)
    snap = store.load_snapshot(tree.dir_code_path(project))
    assert snap.code == tree.synthetic_doc(tree.children(project))  # real children, not the virtual pass-2 edits
    assert "(unpushed)" in dir_prose.read_text()  # the user's edit survived
    assert asyncio.run(tree.propagate_up(engine, a)).synced == []  # and nothing spurious happens next time
