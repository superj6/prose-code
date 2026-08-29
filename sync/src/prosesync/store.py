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
    return Snapshot(prose=data["prose"], code=data["code"], blocks=data["blocks"])
