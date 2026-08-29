"""Collect seed source files for synthetic data generation.

    .venv/bin/python ml/src/data/seed_corpus.py --dirs ~/project/ml --out ml/data/seed/ --max 200

Walks local directories (your own repos are the best seeds: they match what you will edit),
keeps permissively sized files (20-300 lines) in the supported languages, skips vendored /
generated paths, dedupes by content hash, and writes one JSONL manifest plus copies of the files.
HF datasets (The Stack v2, CodeSearchNet) plug in here later via --hf.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

LANG = {".py": "python", ".ts": "typescript", ".tsx": "tsx", ".js": "javascript", ".go": "go", ".rs": "rust", ".java": "java"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", "outputs", "wandb", ".prose", "site-packages", "vendor", "target"}


def collect(dirs: list[str], min_lines: int, max_lines: int, limit: int) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for d in dirs:
        for root, subdirs, files in os.walk(os.path.expanduser(d)):
            subdirs[:] = [s for s in subdirs if s not in SKIP_DIRS and not s.startswith(".")]
            for name in sorted(files):
                ext = Path(name).suffix
                if ext not in LANG or name.endswith((".min.js", ".d.ts", "_pb2.py")) or name.startswith("test_"):
                    continue
                path = Path(root) / name
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                n = text.count("\n")
                if not (min_lines <= n <= max_lines) or "\t\t\t\t\t" in text and ext != ".go":
                    continue
                h = hashlib.sha1(text.encode()).hexdigest()
                if h in seen:
                    continue
                seen.add(h)
                out.append({"id": h[:12], "path": str(path), "language": LANG[ext], "lines": n})
                if len(out) >= limit:
                    return out
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dirs", nargs="+", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--min-lines", type=int, default=20)
    p.add_argument("--max-lines", type=int, default=300)
    p.add_argument("--max", type=int, default=200)
    args = p.parse_args()
    items = collect(args.dirs, args.min_lines, args.max_lines, args.max)
    out = Path(args.out)
    (out / "files").mkdir(parents=True, exist_ok=True)
    for it in items:
        dst = out / "files" / f"{it['id']}{Path(it['path']).suffix}"
        dst.write_text(Path(it["path"]).read_text(encoding="utf-8"))
        it["file"] = str(dst)
    (out / "manifest.jsonl").write_text("".join(json.dumps(it) + "\n" for it in items))
    by_lang = {}
    for it in items:
        by_lang[it["language"]] = by_lang.get(it["language"], 0) + 1
    print(json.dumps({"files": len(items), "by_language": by_lang, "out": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
