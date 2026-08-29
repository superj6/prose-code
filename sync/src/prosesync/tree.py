"""Hierarchical prose: a ``DIR.prose`` per directory, kept in sync with its children.

The directory pair reuses the block machinery unchanged. Its "code side" is a synthetic document:
one paragraph per child, containing the child's summary (``# file`` block of a file's prose, or
the ``# dir/`` block of a subdirectory's DIR.prose), each introduced by ``## <name>``. Language
``prosetree`` has no grammar, so paragraphs are the units and the partition is one block per child.

* ``generate_dir``   : build the synthetic doc and generate DIR.prose (+ snapshot).
* ``propagate_up``   : after a file (or directory) changed, re-sync each ancestor whose synthetic
                       doc changed, bottom-up. Stops as soon as an ancestor's summary is unchanged.
* ``propagate_down`` : the user edited DIR.prose -> sync the synthetic side (child summaries) ->
                       write each changed summary into the child and run a broad file-level sync.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import blocks as B
from . import store
from .align import NeedsRegenerate
from .engine import Engine, new_request_id, pair_id_for
from .models import Change, Pair, Snapshot, SyncOptions, SyncRequest, SyncResponse

DIR_PROSE = "DIR.prose"
DIR_LANGUAGE = "prosetree"
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", "outputs", ".prose", "wandb"}


def dir_code_path(d: Path) -> Path:
    """Pseudo code path of a directory pair (used for the map file and pair id)."""
    return d / "DIR"


def dir_prose_path(d: Path) -> Path:
    return d / DIR_PROSE


def summary_text(prose: str) -> str | None:
    """Body of the leading ``# name`` summary block (heading line removed), or None."""
    ranges = B.segment_prose(prose)
    if not ranges or not B.is_summary_paragraph(prose, ranges[0]):
        return None
    lines = [ln for ln in B.split_lines(prose)[ranges[0][0] : ranges[0][1]] if ln.strip()]
    return "\n".join(lines[1:]).strip() or None


def replace_summary(prose: str, heading: str, body: str) -> str:
    """Return ``prose`` with its summary block replaced by ``# heading`` + body (added if absent)."""
    lines = B.split_lines(prose)
    ranges = B.segment_prose(prose)
    new_block = [f"# {heading}"] + [ln.rstrip() for ln in body.strip().split("\n") if ln.strip()]
    if ranges and B.is_summary_paragraph(prose, ranges[0]):
        s, e = ranges[0]
        trailing = e - s - len([ln for ln in lines[s:e] if ln.strip()])
        lines[s:e] = new_block + [""] * max(1, trailing)
    else:
        lines = new_block + [""] + lines
    return B.join_lines(lines)


@dataclass
class Child:
    name: str          # "calc.py" or "sub/"
    kind: str          # file | dir
    prose_path: Path
    code_path: Path    # the file, or the dir's pseudo code path


def children(d: Path, sidecar_dir: str = "") -> list[Child]:
    out: list[Child] = []
    for entry in sorted(d.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith(".") or entry.name in SKIP_DIRS or entry.name == DIR_PROSE:
            continue
        if entry.is_dir():
            if dir_prose_path(entry).exists():
                out.append(Child(entry.name + "/", "dir", dir_prose_path(entry), dir_code_path(entry)))
        elif entry.is_file() and not entry.name.endswith(".prose"):
            pp = store.prose_path(entry, sidecar_dir)
            if pp.exists():
                out.append(Child(entry.name, "file", pp, entry))
    return out


def first_sentence(text: str | None, limit: int = 160) -> str:
    if not text:
        return "(no summary yet)"
    flat = " ".join(text.split())
    m = re.match(r"(.+?[.!?])(\s|$)", flat)
    out = m.group(1) if m else flat
    return out if len(out) <= limit else out[: limit - 1] + "…"


def _prose_text(path: Path, committed: bool) -> str:
    """A prose file's text; with ``committed`` the git HEAD version when available (the last synced
    state on a fresh clone, where the maps are absent)."""
    if committed:
        head = store.git_head_text(path)
        if head is not None:
            return head
    return path.read_text()


def child_header(name: str) -> str:
    return f"## child: {name}"


def synthetic_doc(kids: Iterable[Child], sidecar_dir: str = "", committed: bool = False) -> str:
    """The directory pair's "code side": one block per immediate child holding the child's WHOLE
    prose - a file's prose file, or a subdirectory's DIR.prose (which already encapsulates its own
    subtree). Blocks start with ``## child: <name>``."""
    parts = []
    for c in kids:
        body = _prose_text(c.prose_path, committed).strip("\n") or "(no prose yet)"
        parts.append(f"{child_header(c.name)}\n{body}\n")
    return "\n".join(parts)


@dataclass
class TreeResult:
    generated: list[Path] = field(default_factory=list)
    synced: list[tuple[Path, int]] = field(default_factory=list)   # (DIR.prose, number of line edits)
    unchanged: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)


async def generate_dir(engine: Engine, d: Path, sidecar_dir: str = "") -> Path | None:
    kids = children(d, sidecar_dir)
    if not kids:
        return None
    doc = synthetic_doc(kids, sidecar_dir)
    resp = await engine.generate_free(doc, DIR_LANGUAGE, str(dir_code_path(d)), title=d.name + "/")
    out = dir_prose_path(d)
    out.write_text(resp.prose)
    store.save_snapshot(dir_code_path(d), Snapshot(prose=resp.prose, code=doc, blocks=resp.blocks, code_blocks=resp.code_blocks))
    return out


async def generate_tree(engine: Engine, root: Path, sidecar_dir: str = "", overwrite: bool = False) -> TreeResult:
    """Generate prose for every supported file under ``root`` (skipping existing ones unless
    ``overwrite``), then DIR.prose for every directory, deepest first."""
    result = TreeResult()
    dirs: list[Path] = []
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS or part.startswith(".") for part in path.relative_to(root).parts[:-1]):
            continue
        if path.is_dir():
            if path.name not in SKIP_DIRS and not path.name.startswith("."):
                dirs.append(path)
        elif path.suffix in store.LANGUAGE_SUFFIXES and not path.name.endswith(".prose"):
            pp = store.prose_path(path, sidecar_dir)
            if pp.exists() and not overwrite:
                continue
            try:
                resp = await engine.generate_prose(path.read_text(), store.language_for(path), str(path))
            except Exception as e:  # noqa: BLE001 - keep going
                result.errors.append((path, f"{type(e).__name__}: {e}"))
                continue
            pp.parent.mkdir(parents=True, exist_ok=True)
            pp.write_text(resp.prose)
            store.save_snapshot(path, Snapshot(prose=resp.prose, code=path.read_text(), blocks=resp.blocks, code_blocks=resp.code_blocks))
            result.generated.append(pp)
    for d in sorted(dirs + [root], key=lambda p: -len(p.parts)):  # deepest first
        try:
            out = await generate_dir(engine, d, sidecar_dir)
        except Exception as e:  # noqa: BLE001
            result.errors.append((d, f"{type(e).__name__}: {e}"))
            continue
        if out is not None:
            result.generated.append(out)
    return result


def load_dir_snapshot(d: Path, sidecar_dir: str = "", min_block_lines: int = 3) -> Snapshot | None:
    """The directory pair's snapshot; rebuilt from the checked-in DIR.prose when the map is absent
    (maps live in the gitignored .prose/ directory)."""
    snap = store.load_snapshot(dir_code_path(d))
    if snap is not None:
        return snap
    prose_path = dir_prose_path(d)
    if not prose_path.exists():
        return None
    prose = store.git_head_text(prose_path)  # the committed DIR.prose is the last synced state
    committed = prose is not None
    if prose is None:
        prose = prose_path.read_text()
    doc = synthetic_doc(children(d, sidecar_dir), sidecar_dir, committed=committed)  # ...and so are the committed child summaries
    snap = Snapshot(prose=prose, code=doc, blocks=B.side_partition(prose, "prose", prefix="p"),
                    code_blocks=B.side_partition(doc, "code", DIR_LANGUAGE, prefix="b"))
    store.save_snapshot(dir_code_path(d), snap)
    return snap


async def _sync_dir(engine: Engine, d: Path, changed: str, sidecar_dir: str, broad: bool = False, save: bool = True) -> SyncResponse | None:
    """Sync the directory pair; returns None when there is nothing to do or no map exists."""
    prose_path = dir_prose_path(d)
    base = load_dir_snapshot(d, sidecar_dir, engine.min_block_lines)
    if base is None or not prose_path.exists():
        return None
    if not base.code_blocks:  # snapshot from the old paired format: re-partition both sides
        base = Snapshot(prose=base.prose, code=base.code, blocks=B.side_partition(base.prose, "prose", prefix="p"),
                        code_blocks=B.side_partition(base.code, "code", DIR_LANGUAGE, prefix="b"))
    doc = synthetic_doc(children(d, sidecar_dir), sidecar_dir)
    prose = prose_path.read_text()
    if changed == "code" and doc == base.code:
        return None
    if changed == "prose" and prose == base.prose:
        return None
    other_dirty = (prose != base.prose) if changed == "code" else (doc != base.code)
    req = SyncRequest(
        request_id=new_request_id(),
        pair=Pair(pair_id=pair_id_for(str(dir_code_path(d))), language=DIR_LANGUAGE, code_path=str(dir_code_path(d)), prose=prose, code=doc, mode="free"),
        base=base, change=Change(side=changed), other_side_dirty=other_dirty, options=SyncOptions(broad=broad),
    )
    resp = await engine.sync(req)
    prose_path.write_text(resp.prose)
    if save:
        # For an upward sync the snapshot must hold the REAL synthetic document: when DIR.prose had
        # unpushed user edits, pass 2 edited child summaries only virtually (resp.code); recording
        # that would make the next propagation mistake the unchanged children for a change.
        code = doc if changed == "code" else resp.code
        store.save_snapshot(dir_code_path(d), Snapshot(prose=resp.prose, code=code, blocks=resp.blocks, code_blocks=resp.code_blocks))
    return resp


async def propagate_up(engine: Engine, code_path: Path, sidecar_dir: str = "", max_levels: int = 5) -> TreeResult:
    """Re-sync ancestors of ``code_path`` whose DIR.prose exists, stopping when a level is unchanged."""
    result = TreeResult()
    d = code_path.parent
    for _ in range(max_levels):
        if not dir_prose_path(d).exists():
            break
        before = dir_prose_path(d).read_text()
        try:
            resp = await _sync_dir(engine, d, "code", sidecar_dir)
        except NeedsRegenerate as e:
            result.errors.append((dir_prose_path(d), f"needs regenerate: {e}"))
            break
        if resp is None:
            result.unchanged.append(dir_prose_path(d))
            break
        result.synced.append((dir_prose_path(d), len(resp.line_edits)))
        if any("unpushed" in w for w in resp.warnings):
            result.errors.append((dir_prose_path(d), "has unpushed edits; run push-down to apply them to the children"))
        if resp.prose == before or d.parent == d:
            break  # this DIR.prose did not change: parents (which see the whole file) are unaffected
        d = d.parent
    return result


async def propagate_down(engine: Engine, d: Path, sidecar_dir: str = "", depth: int = 3) -> TreeResult:
    """The user edited DIR.prose: update the children whose paragraphs changed."""
    result = TreeResult()
    base = load_dir_snapshot(d, sidecar_dir, engine.min_block_lines)
    if base is None:
        result.errors.append((dir_prose_path(d), "no DIR.prose (or it cannot be paired with the children); run gen first"))
        return result
    old_doc = base.code
    # The directory snapshot is saved only once every child push succeeded, so a failed push
    # (timeout, bad model output) is retried by the next push-down instead of being lost.
    resp = await _sync_dir(engine, d, "prose", sidecar_dir, save=False)
    if resp is None:
        result.unchanged.append(dir_prose_path(d))
        return result
    result.synced.append((dir_prose_path(d), len(resp.line_edits)))
    kids = {c.name: c for c in children(d, sidecar_dir)}
    old_parts = _doc_sections(old_doc)
    for name, body in _doc_sections(resp.code).items():
        if old_parts.get(name) == body:
            continue
        if name not in kids:
            try:
                created = await _create_child(engine, d, name, body, sidecar_dir)
            except Exception as e:  # noqa: BLE001
                result.errors.append((d / name, f"could not create: {type(e).__name__}: {e}"))
                continue
            if created is not None:
                result.generated.append(created)
            continue
        child = kids[name]
        if child.kind == "dir" and depth <= 0:
            continue
        try:
            await _push_child(engine, child, body, sidecar_dir, depth - 1, result)
        except Exception as e:  # noqa: BLE001
            result.errors.append((child.code_path, f"{type(e).__name__}: {e}"))
    if not result.errors:
        doc = synthetic_doc(children(d, sidecar_dir), sidecar_dir)
        store.save_snapshot(dir_code_path(d), Snapshot(prose=resp.prose, code=doc, blocks=resp.blocks,
                                                       code_blocks=B.side_partition(doc, "code", DIR_LANGUAGE, prefix="b")))
    return result


def _with_heading(name: str, body: str) -> str:
    """Ensure a child's prose starts with its `# name` summary heading."""
    text = body.strip("\n")
    if text.startswith("# ") and not text.startswith("## "):
        return text + "\n"
    return f"# {name}\n{text}\n"


async def _create_child(engine: Engine, d: Path, name: str, body: str, sidecar_dir: str) -> Path | None:
    """Create a file (code generated from the prose the model wrote for it) or a directory (with
    that prose as its DIR.prose) that DIR.prose describes but the filesystem lacks."""
    if name.endswith("/"):
        sub = d / name.rstrip("/")
        sub.mkdir(parents=True, exist_ok=True)
        out = dir_prose_path(sub)
        if not out.exists():
            out.write_text(_with_heading(name, body))
        return out
    code_path = d / name
    if code_path.suffix not in store.LANGUAGE_SUFFIXES or code_path.exists():
        return None
    prose_path = store.prose_path(code_path, sidecar_dir)
    prose, code, blocks, code_blocks = await engine.create_from_prose(_with_heading(name, body), store.language_for(code_path), str(code_path))
    prose_path.parent.mkdir(parents=True, exist_ok=True)
    prose_path.write_text(prose)
    code_path.write_text(code)
    store.save_snapshot(code_path, Snapshot(prose=prose, code=code, blocks=blocks, code_blocks=code_blocks))
    return code_path


def _doc_sections(doc: str) -> dict[str, str]:
    """``## child: name`` -> that child's prose text."""
    out: dict[str, str] = {}
    name, buf = None, []
    for ln in B.split_lines(doc):
        m = B.CHILD_HEADER_RE.match(ln)
        if m:
            if name is not None:
                out[name] = "\n".join(buf).strip("\n")
            name, buf = m.group(1).strip(), []
        elif name is not None:
            buf.append(ln)
    if name is not None:
        out[name] = "\n".join(buf).strip("\n")
    return out


async def _push_child(engine: Engine, child: Child, new_prose: str, sidecar_dir: str, depth: int, result: TreeResult) -> None:
    """The directory view rewrote this child's prose: write it back and sync it into the code."""
    prose_before = child.prose_path.read_text()
    prose_now = _with_heading(child.name, new_prose)
    if prose_now.strip() == prose_before.strip():
        return
    if child.kind == "dir":
        child.prose_path.write_text(prose_now)
        if depth > 0:
            sub = await propagate_down(engine, child.code_path.parent, sidecar_dir, depth)
            result.synced.extend(sub.synced)
            result.errors.extend(sub.errors)
        return
    base = store.load_snapshot(child.code_path)
    code = child.code_path.read_text()
    if base is None:
        base = store.rebuild_snapshot(child.code_path, child.prose_path, store.language_for(child.code_path), engine.file_mode, engine.min_block_lines)
        if base is None:
            result.errors.append((child.code_path, "no map and prose cannot be paired"))
            return
    req = SyncRequest(
        request_id=new_request_id(),
        pair=Pair(pair_id=pair_id_for(str(child.code_path)), language=store.language_for(child.code_path), code_path=str(child.code_path), prose=prose_now, code=code),
        base=base, change=Change(side="prose"), other_side_dirty=(code != base.code), options=SyncOptions(broad=True),
    )
    resp = await engine.sync(req)
    edits = len(resp.line_edits)
    if resp.line_edits:
        # The code changed to honour the new prose; refresh the paragraphs describing the changed
        # code blocks (a plain code -> prose sync against the pre-push code).
        try:
            follow = SyncRequest(
                request_id=new_request_id(),
                pair=Pair(pair_id=req.pair.pair_id, language=req.pair.language, code_path=req.pair.code_path, prose=resp.prose, code=resp.code),
                base=Snapshot(prose=resp.prose, code=code, blocks=resp.blocks, code_blocks=base.code_blocks or B.side_partition(code, "code", req.pair.language, prefix="b")),
                change=Change(side="code"),
            )
            resp2 = await engine.sync(follow)
            resp, edits = resp2, edits + len(resp2.line_edits)
        except NeedsRegenerate as e:
            result.errors.append((child.code_path, f"paragraph refresh skipped: {e}"))
    child.prose_path.write_text(resp.prose)
    child.code_path.write_text(resp.code)
    store.save_snapshot(child.code_path, Snapshot(prose=resp.prose, code=resp.code, blocks=resp.blocks, code_blocks=resp.code_blocks))
    result.synced.append((child.code_path, edits))


__all__ = ["child_header", "children", "generate_dir", "generate_tree", "propagate_down", "propagate_up", "summary_text", "synthetic_doc"]
