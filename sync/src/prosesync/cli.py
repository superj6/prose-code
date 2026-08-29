"""Command line entry points.

    prosesync gen   examples/calc.py                 # write calc.py.prose + .prose/calc.py.map.json
    prosesync sync  examples/calc.py --changed code  # update the prose after editing the code
    prosesync sync  examples/calc.py --changed prose # update the code after editing the prose
    prosesync gen   src/                             # every file under src/ + a DIR.prose per directory
    prosesync gen   examples/new_thing.py.prose     # the inverse: write a prose file first, get the code
    prosesync push-down src/                         # apply edits made in src/DIR.prose to the children
    prosesync check-model                            # verify sync.model exists on the endpoint
    prosesync serve                                  # HTTP server for the VS Code extension
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import REPO_ROOT, load_config


def _env() -> None:
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv()  # cwd, if different


def _engine(args):
    from .backends import get_backend
    from .engine import Engine

    cfg = load_config(args.config, args.override)
    backend = get_backend(cfg, args.backend)
    return cfg, Engine(cfg, backend)


async def cmd_gen(args) -> int:
    from . import store
    from .models import Snapshot

    _cfg, engine = _engine(args)
    code_path = Path(args.file).resolve()
    if code_path.is_dir():
        from .tree import generate_tree

        result = await generate_tree(engine, code_path, args.sidecar_dir, overwrite=args.overwrite)
        for pth in result.generated:
            print(f"wrote {pth}", file=sys.stderr)
        for pth, err in result.errors:
            print(f"error {pth}: {err}", file=sys.stderr)
        print(f"{len(result.generated)} file(s) written, {len(result.errors)} error(s)", file=sys.stderr)
        return 1 if result.errors else 0
    if code_path.name.endswith(".prose"):
        return await _gen_code_from_prose(engine, code_path, args)
    code = code_path.read_text()
    language = args.lang or store.language_for(code_path)
    resp = await engine.generate_prose(code, language, str(code_path))
    out = store.prose_path(code_path, args.sidecar_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(resp.prose)
    store.save_snapshot(code_path, Snapshot(prose=resp.prose, code=code, blocks=resp.blocks, code_blocks=resp.code_blocks))
    print(f"wrote {out} ({len(resp.blocks)} blocks, {resp.latency_ms} ms, model={resp.model})", file=sys.stderr)
    if args.print:
        print(resp.prose)
    if not args.no_propagate:
        await _propagate(engine, code_path, args.sidecar_dir)
    return 0


async def _gen_code_from_prose(engine, prose_path: Path, args) -> int:
    """The inverse: FILE.prose -> FILE (refuses to overwrite an existing code file)."""
    from . import store
    from .models import Snapshot

    code_path = prose_path.with_name(prose_path.name[: -len(".prose")])
    if code_path.exists() and not args.overwrite:
        print(f"{code_path} exists; use `prosesync sync {code_path.name} --changed prose` to update it, or --overwrite", file=sys.stderr)
        return 2
    language = args.lang or store.language_for(code_path)
    prose, code, blocks, code_blocks = await engine.create_from_prose(prose_path.read_text(), language, str(code_path))
    code_path.write_text(code)
    prose_path.write_text(prose)
    store.save_snapshot(code_path, Snapshot(prose=prose, code=code, blocks=blocks, code_blocks=code_blocks))
    print(f"wrote {code_path} ({len(blocks)} blocks)", file=sys.stderr)
    if args.print:
        print(code)
    if not args.no_propagate:
        await _propagate(engine, code_path, args.sidecar_dir)
    return 0


async def _propagate(engine, code_path: Path, sidecar_dir: str) -> None:
    from .tree import propagate_up

    up = await propagate_up(engine, code_path, sidecar_dir)
    for pth, n in up.synced:
        print(f"  propagated: {pth} ({n} edit(s))", file=sys.stderr)
    for pth, err in up.errors:
        print(f"  propagate error {pth}: {err}", file=sys.stderr)


async def cmd_sync(args) -> int:
    from . import store
    from .engine import NeedsRegenerate, new_request_id, pair_id_for
    from .models import Change, Pair, Snapshot, SyncRequest

    cfg, engine = _engine(args)
    code_path = Path(args.file).resolve()
    prose_file = store.prose_path(code_path, args.sidecar_dir)
    if not prose_file.exists():
        print(f"{prose_file} does not exist; run `prosesync gen` first", file=sys.stderr)
        return 2
    code, prose = code_path.read_text(), prose_file.read_text()
    language = args.lang or store.language_for(code_path)
    base = store.load_snapshot(code_path)
    if base is None:
        base = store.rebuild_snapshot(code_path, prose_file, language, engine.file_mode, int(cfg.segment.min_block_lines))
        if base is None:
            print("no snapshot map and the prose cannot be paired with the code; run `prosesync gen`", file=sys.stderr)
            return 2
        print(f"rebuilt block map ({len(base.blocks)} prose / {len(base.code_blocks)} code blocks)", file=sys.stderr)
        if base.prose == prose and base.code == code:
            print("nothing to sync yet", file=sys.stderr)
            return 0
    other_dirty = (prose != base.prose) if args.changed == "code" else (code != base.code)
    req = SyncRequest(
        request_id=new_request_id(),
        pair=Pair(pair_id=pair_id_for(str(code_path)), language=language, code_path=str(code_path), prose=prose, code=code),
        base=base, change=Change(side=args.changed), other_side_dirty=other_dirty,
    )

    async def show(le):
        print(f"  {le.side} {le.block} lines {le.start}-{le.end}: {le.reason or ''}", file=sys.stderr)

    try:
        resp = await engine.sync(req, on_line_edit=show)
    except NeedsRegenerate as e:
        print(f"cannot realign ({e}); run `prosesync gen` to regenerate", file=sys.stderr)
        return 3
    for w in resp.warnings:
        print(f"  warning: {w}", file=sys.stderr)
    if args.dry_run:
        print(resp.prose if resp.target_side == "prose" else resp.code)
    else:
        prose_file.write_text(resp.prose)
        code_path.write_text(resp.code)
        store.save_snapshot(code_path, Snapshot(prose=resp.prose, code=resp.code, blocks=resp.blocks, code_blocks=resp.code_blocks))
    print(f"{len(resp.line_edits)} edit(s) to {resp.target_side} in {resp.latency_ms} ms (model={resp.model})", file=sys.stderr)
    if not args.dry_run and not args.no_propagate:
        await _propagate(engine, code_path, args.sidecar_dir)
    return 0


async def cmd_push_down(args) -> int:
    from .tree import propagate_down

    _cfg, engine = _engine(args)
    d = Path(args.dir).resolve()
    result = await propagate_down(engine, d, args.sidecar_dir)
    for pth, n in result.synced:
        print(f"  synced: {pth} ({n} edit(s))", file=sys.stderr)
    for pth in result.unchanged:
        print(f"  unchanged: {pth}", file=sys.stderr)
    for pth, err in result.errors:
        print(f"  error {pth}: {err}", file=sys.stderr)
    return 1 if result.errors else 0


async def cmd_check_model(args) -> int:
    _cfg, engine = _engine(args)
    if not hasattr(engine.backend, "check_model"):
        print(f"backend {engine.backend.name} has no model check")
        return 0
    info = await engine.backend.check_model()
    print(f"ok: {info.get('id')} (owned_by={info.get('owned_by')})")
    return 0


def cmd_serve(args) -> int:
    from .server import serve

    cfg = load_config(args.config, args.override)
    return serve(cfg, backend_name=args.backend, port=args.port)


def main(argv=None) -> int:
    _env()
    p = argparse.ArgumentParser(prog="prosesync")
    p.add_argument("--config", default=None, help="YAML config (default configs/base.yaml)")
    p.add_argument("--override", nargs="*", default=None, help="dotlist overrides, e.g. sync.model=gpt-x")
    p.add_argument("--backend", default=None, help="openai | mock")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen", help="FILE -> FILE.prose; DIR -> every file under it plus DIR.prose per directory")
    g.add_argument("file"); g.add_argument("--lang"); g.add_argument("--print", action="store_true")
    g.add_argument("--sidecar-dir", default=""); g.add_argument("--overwrite", action="store_true", help="DIR mode: regenerate existing prose too; FILE.prose mode: overwrite the code file")
    g.add_argument("--no-propagate", action="store_true", help="do not update ancestor DIR.prose files")
    s = sub.add_parser("sync"); s.add_argument("file"); s.add_argument("--changed", choices=["code", "prose"], required=True)
    s.add_argument("--lang"); s.add_argument("--dry-run", action="store_true"); s.add_argument("--sidecar-dir", default="")
    s.add_argument("--no-propagate", action="store_true", help="do not update ancestor DIR.prose files")
    pd = sub.add_parser("push-down", help="apply edits made in DIR/DIR.prose to the children"); pd.add_argument("dir"); pd.add_argument("--sidecar-dir", default="")
    sub.add_parser("check-model")
    sv = sub.add_parser("serve"); sv.add_argument("--port", type=int, default=None)

    args = p.parse_args(argv)
    if args.cmd == "gen":
        return asyncio.run(cmd_gen(args))
    if args.cmd == "sync":
        return asyncio.run(cmd_sync(args))
    if args.cmd == "push-down":
        return asyncio.run(cmd_push_down(args))
    if args.cmd == "check-model":
        return asyncio.run(cmd_check_model(args))
    if args.cmd == "serve":
        return cmd_serve(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
