"""Mine realistic code edits from git history: small commits that touch one supported file with
1-3 hunks give (C0, C1) pairs. Emits a manifest of candidate edits; perturb-style labelling
(generate prose for C0, then production Engine.sync for the code change) is done by
``label_git_edits`` so the records match the serving distribution.

    .venv/bin/python ml/src/data/git_mine.py --repos ~/project/foo ~/project/bar --out ml/data/git_edits.jsonl [--max 500]
    .venv/bin/python ml/src/data/git_mine.py --label ml/data/git_edits.jsonl --out ml/data/git_records.jsonl [--backend mock]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
for _p in (REPO / "sync" / "src", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ml.src.data.records import (
    read_jsonl,
    record_id,
    validate,
    write_jsonl,
)
from ml.src.data.seed_corpus import LANG


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


def mine(repo: str, max_hunks: int, min_lines: int, max_lines: int, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    log = _git(repo, "log", "--no-merges", "--format=%H%x00%s", "--numstat", "-n", "2000")
    commit, subject, files = None, "", []
    entries = []
    for line in log.splitlines():
        if "\x00" in line:
            if commit and len(files) == 1:
                entries.append((commit, subject, files[0]))
            commit, subject = line.split("\x00", 1)
            files = []
        elif line.strip():
            parts = line.split("\t")
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                files.append((int(parts[0]), int(parts[1]), parts[2]))
    if commit and len(files) == 1:
        entries.append((commit, subject, files[0]))
    for sha, subj, (added, deleted, path) in entries:
        if Path(path).suffix not in LANG or added + deleted > 40 or added + deleted == 0:
            continue
        try:
            new = _git(repo, "show", f"{sha}:{path}")
            old = _git(repo, "show", f"{sha}^:{path}")
        except subprocess.CalledProcessError:
            continue  # added/renamed file
        n = old.count("\n")
        if not (min_lines <= n <= max_lines):
            continue
        hunks = _git(repo, "diff", "--unified=0", f"{sha}^", sha, "--", path).count("\n@@")
        if not (1 <= hunks <= max_hunks):
            continue
        out.append({"id": hashlib.sha1(f"{repo}{sha}{path}".encode()).hexdigest()[:12], "repo": repo, "commit": sha, "subject": subj,
                    "path": path, "language": LANG[Path(path).suffix], "code": old, "code_now": new, "hunks": hunks, "lines": n})
        if len(out) >= limit:
            break
    return out


async def label(edits: list[dict[str, Any]], backend: str | None, config: str | None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    from dotenv import load_dotenv
    from prosesync.align import NeedsRegenerate
    from prosesync.backends import get_backend
    from prosesync.config import load_config
    from prosesync.engine import Engine
    from prosesync.interaction_log import InteractionLog
    from prosesync.models import Change, Pair, Snapshot, SyncRequest
    from prosesync.verify.treesitter import first_error

    load_dotenv(REPO / ".env")
    cfg = load_config(config)
    engine = Engine(cfg, get_backend(cfg, backend), InteractionLog(None, enabled=False))
    stats = {"edits": len(edits), "kept": 0, "bad_syntax": 0, "no_edits": 0, "failed": 0}
    out = []
    for e in edits:
        ok = first_error(e["language"], e["code_now"])
        if ok is not None and not ok[0]:
            stats["bad_syntax"] += 1
            continue
        try:
            gen = await engine.generate(e["code"], e["language"], e["path"])
            req = SyncRequest(request_id=f"git-{e['id']}", pair=Pair(mode="paired", pair_id=f"git-{e['id']}", language=e["language"], code_path=e["path"], prose=gen.prose, code=e["code_now"]),
                              base=Snapshot(prose=gen.prose, code=e["code"], blocks=gen.blocks), change=Change(side="code"))
            resp = await engine.sync(req)
        except (NeedsRegenerate, Exception) as err:  # noqa: BLE001 - keep going on a bad item
            stats["failed"] += 1
            print(json.dumps({"id": e["id"], "error": f"{type(err).__name__}: {err}"}), file=sys.stderr)
            continue
        if not resp.line_edits or any("rejected" in w for w in resp.warnings) or any(le.block == "*" for le in resp.line_edits):
            stats["no_edits"] += 1
            continue
        rec = {"source": "git", "language": e["language"], "prompt_version": engine.prompt_version, "prose": gen.prose, "code": e["code"],
               "blocks": [b.model_dump() for b in gen.blocks], "changed_side": "code", "code_now": e["code_now"], "other_side_dirty": False,
               "target_edits": [{"op": "replace" if le.new_text else "delete", "block": le.block, "text": le.new_text or None, "reason": le.reason or ""} for le in resp.line_edits],
               "prose_after": resp.prose, "code_after": resp.code,
               "meta": {"seed": e["id"], "repo": e["repo"], "commit": e["commit"], "subject": e["subject"], "model": resp.model, "latency_ms": resp.latency_ms}}
        if validate(rec):
            stats["failed"] += 1
            continue
        rec["id"] = record_id(rec)
        out.append(rec)
        stats["kept"] += 1
    return out, stats


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repos", nargs="*", default=[])
    p.add_argument("--label", default=None, help="edits JSONL to label into records")
    p.add_argument("--out", required=True)
    p.add_argument("--max", type=int, default=500)
    p.add_argument("--max-hunks", type=int, default=3)
    p.add_argument("--min-lines", type=int, default=20)
    p.add_argument("--max-lines", type=int, default=300)
    p.add_argument("--backend", default=None)
    p.add_argument("--config", default=None)
    args = p.parse_args()
    if args.label:
        records, stats = asyncio.run(label(list(read_jsonl(Path(args.label))), args.backend, args.config))
        write_jsonl(Path(args.out), records)
        print(json.dumps(stats))
        return 0
    edits: list[dict[str, Any]] = []
    for repo in args.repos:
        try:
            got = mine(str(Path(repo).expanduser()), args.max_hunks, args.min_lines, args.max_lines, args.max - len(edits))
        except subprocess.CalledProcessError as e:
            print(json.dumps({"repo": repo, "error": (e.stderr or "").strip().splitlines()[-1:] or "git failed"}), file=sys.stderr)
            continue
        edits.extend(got)
        print(json.dumps({"repo": repo, "edits": len(got)}), file=sys.stderr)
    write_jsonl(Path(args.out), edits)
    print(json.dumps({"edits": len(edits), "by_language": {lang: sum(1 for e in edits if e["language"] == lang) for lang in {e["language"] for e in edits}}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
