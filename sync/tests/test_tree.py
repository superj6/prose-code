import asyncio
import shutil

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
    assert doc.startswith("## a.py\nMock summary of the file.\n\n## sub/\nMock summary of the directory.\n- b.ts: Mock summary of the file.\n")
    root_prose = (project / "DIR.prose").read_text()
    assert root_prose.startswith("# proj/\nMock summary of the directory.\n\nCovers ## a.py in this directory.\n\nCovers ## sub/")
    snap = store.load_snapshot(tree.dir_code_path(project))
    assert [b.id for b in snap.blocks] == ["s", "p1", "p2"] and [b.id for b in snap.code_blocks] == ["b1", "b2"]
    again = asyncio.run(tree.generate_tree(engine, project))
    assert sorted(p.name for p in again.generated) == ["DIR.prose", "DIR.prose"]


def test_propagate_up_updates_ancestors(engine, project):
    asyncio.run(tree.generate_tree(engine, project))
    b = project / "sub" / "b.ts"
    prose_path = store.prose_path(b)
    prose_path.write_text(tree.replace_summary(prose_path.read_text(), "b.ts", "Now a totally different summary."))
    result = asyncio.run(tree.propagate_up(engine, b))
    assert [p.relative_to(project).as_posix() for p, _ in result.synced] == ["sub/DIR.prose"]
    assert "(updated)" in (project / "sub" / "DIR.prose").read_text()
    assert asyncio.run(tree.propagate_up(engine, b)).synced == []


def test_propagate_down_pushes_summary_into_child(engine, project):
    asyncio.run(tree.generate_tree(engine, project))
    dir_prose = project / "DIR.prose"
    dir_prose.write_text(dir_prose.read_text().replace("Covers ## a.py in this directory.", "Covers ## a.py in this directory (user wants more)."))
    result = asyncio.run(tree.propagate_down(engine, project))
    synced = [p.relative_to(project).as_posix() for p, _ in result.synced]
    assert synced[0] == "DIR.prose" and "a.py" in synced and not result.errors
    child_prose = (project / "a.py.prose").read_text()
    assert child_prose.startswith("# a.py\n") and "(updated)" in tree.summary_text(child_prose)
    assert "# " in (project / "a.py").read_text()
    assert "(updated)" in child_prose.split("\n\n")[1]


def test_push_down_creates_children_named_in_dir_prose(engine, project):
    asyncio.run(tree.generate_tree(engine, project))
    dir_prose = project / "DIR.prose"
    dir_prose.write_text(dir_prose.read_text().rstrip("\n") + "\n\n## c.py\nA new module that greets people.\n\n## util/\nHelpers shared by the package.\n")
    result = asyncio.run(tree.propagate_down(engine, project))
    assert not result.errors, result.errors
    created = sorted(p.relative_to(project).as_posix() for p in result.generated)
    assert created == ["c.py", "util/DIR.prose"]
    assert (project / "c.py").read_text().startswith("# ")
    assert (project / "c.py.prose").read_text().startswith("# c.py\nA new module that greets people.")
    assert (project / "util" / "DIR.prose").read_text().startswith("# util/\nHelpers shared by the package.")
    assert store.load_snapshot(project / "c.py") is not None


def test_propagate_up_with_dirty_dir_prose_keeps_real_doc_in_snapshot(engine, project):
    asyncio.run(tree.generate_tree(engine, project))
    dir_prose = project / "DIR.prose"
    dir_prose.write_text(dir_prose.read_text().replace("Covers ## sub/", "Covers ## sub/ (unpushed)"))
    a = project / "a.py"
    ap = store.prose_path(a)
    ap.write_text(tree.replace_summary(ap.read_text(), "a.py", "Changed summary."))
    result = asyncio.run(tree.propagate_up(engine, a))
    assert result.synced and any("unpushed" in e for _, e in result.errors)
    snap = store.load_snapshot(tree.dir_code_path(project))
    assert snap.code == tree.synthetic_doc(tree.children(project))
    assert "(unpushed)" in dir_prose.read_text()
    assert asyncio.run(tree.propagate_up(engine, a)).synced == []


def test_dir_snapshot_rebuilt_from_checked_in_prose(engine, project):
    asyncio.run(tree.generate_tree(engine, project))
    shutil.rmtree(project / ".prose")
    shutil.rmtree(project / "sub" / ".prose")
    snap = tree.load_dir_snapshot(project)
    assert snap is not None and [b.id for b in snap.blocks] == ["s", "p1", "p2"] and [b.id for b in snap.code_blocks] == ["b1", "b2"]
    assert snap.code == tree.synthetic_doc(tree.children(project))


def test_replace_summary_roundtrip():
    prose = "# f.py\nOld.\n\n## g\nBody.\n"
    assert tree.replace_summary(prose, "f.py", "New one.") == "# f.py\nNew one.\n\n## g\nBody.\n"
    assert tree.replace_summary("## g\nBody.\n", "f.py", "Added.") == "# f.py\nAdded.\n\n## g\nBody.\n"
    assert tree.summary_text("no summary\n") is None
    assert tree.first_sentence("One thing. Another thing.") == "One thing."


def test_fresh_clone_propagates_from_committed_base(engine, project):
    import subprocess

    asyncio.run(tree.generate_tree(engine, project))
    shutil.rmtree(project / ".prose"); shutil.rmtree(project / "sub" / ".prose")
    run = lambda *a: subprocess.run(["git", "-C", str(project), *a], check=True, capture_output=True)
    run("init", "-q"); run("config", "user.email", "t@t"); run("config", "user.name", "t"); run("add", "."); run("commit", "-q", "-m", "clone")
    a = project / "a.py"
    ap = store.prose_path(a)
    ap.write_text(tree.replace_summary(ap.read_text(), "a.py", "Changed after the clone."))  # e.g. a file sync just refreshed the summary
    result = asyncio.run(tree.propagate_up(engine, a))
    assert [p.name for p, _ in result.synced] == ["DIR.prose"], (result.synced, result.unchanged, result.errors)
    assert "(updated)" in (project / "DIR.prose").read_text()
