"""Sidecar files: ``foo.py`` <-> ``foo.py.prose`` and the block map ``.prose/foo.py.map.json``.

The map holds the last synced snapshot of both sides, so a later ``sync`` can diff against it.
Used by the CLI; the extension keeps the same structure in memory and persists it the same way.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import Snapshot

MAP_VERSION = 1

_EXT_TO_LANGUAGE = {
    ".py": "python", ".ts": "typescript", ".tsx": "tsx", ".js": "javascript", ".jsx": "jsx", ".go": "go",
    ".rs": "rust", ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".rb": "ruby",
    ".cs": "c_sharp", ".kt": "kotlin", ".swift": "swift", ".php": "php", ".sh": "bash", ".lua": "lua",
}


LANGUAGE_SUFFIXES = set(_EXT_TO_LANGUAGE)


def language_for(path: Path) -> str:
    return _EXT_TO_LANGUAGE.get(path.suffix.lower(), path.suffix.lstrip(".").lower() or "text")


def prose_path(code_path: Path, sidecar_dir: str = "") -> Path:
    if sidecar_dir:
        return code_path.parent / sidecar_dir / (code_path.name + ".prose")
    return code_path.with_name(code_path.name + ".prose")


def map_path(code_path: Path) -> Path:
    return code_path.parent / ".prose" / (code_path.name + ".map.json")


def git_head_text(path: Path) -> str | None:
    """The committed (HEAD) content of ``path``, or None when not in git / untracked / no git.

    The block maps live in the gitignored .prose/ directory, so on a fresh clone the last synced
    state of a checked-in pair is best approximated by what was committed."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(path.parent), "show", f"HEAD:./{path.name}"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def base_texts(code_path: Path, prose_path: Path) -> tuple[str, str, str]:
    """(prose, code, source) to rebuild a snapshot from when the map is missing: the committed
    versions when available, else the working copies ("git" | "worktree")."""
    prose_head = git_head_text(prose_path)
    code_head = git_head_text(code_path)
    if prose_head is not None and code_head is not None:
        return prose_head, code_head, "git"
    return prose_path.read_text(), code_path.read_text(), "worktree"


def rebuild_snapshot(code_path: Path, prose_path: Path, language: str, mode: str, min_block_lines: int = 3) -> Snapshot | None:
    """Rebuild a file pair's snapshot from the committed (else current) texts when the map is
    missing. Free mode always succeeds (independent partitions); paired mode needs the counts to
    agree."""
    prose, code, _source = base_texts(code_path, prose_path)
    from . import blocks as B

    if mode == "free":
        snap = Snapshot(prose=prose, code=code, blocks=B.side_partition(prose, "prose", prefix="p"),
                        code_blocks=B.side_partition(code, "code", language, prefix="b"))
    else:
        from .align import resegment

        blocks = resegment(prose, code, language, min_block_lines)
        if blocks is None:
            return None
        snap = Snapshot(prose=prose, code=code, blocks=blocks)
    save_snapshot(code_path, snap)
    return snap


def save_snapshot(code_path: Path, snap: Snapshot) -> None:
    p = map_path(code_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": MAP_VERSION, **snap.model_dump()}, indent=1))


def load_snapshot(code_path: Path) -> Snapshot | None:
    p = map_path(code_path)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    if data.get("version") != MAP_VERSION:
        return None
    return Snapshot(prose=data["prose"], code=data["code"], blocks=data["blocks"], code_blocks=data.get("code_blocks", []))
