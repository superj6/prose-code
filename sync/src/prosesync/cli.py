"""Command line entry points.

    prosesync gen   examples/calc.py                 # write calc.py.prose + .prose/calc.py.map.json
    prosesync sync  examples/calc.py --changed code  # update the prose after editing the code
    prosesync sync  examples/calc.py --changed prose # update the code after editing the prose
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
    code = code_path.read_text()
    language = args.lang or store.language_for(code_path)
    resp = await engine.generate(code, language, str(code_path))
    out = store.prose_path(code_path, args.sidecar_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(resp.prose)
    store.save_snapshot(code_path, Snapshot(prose=resp.prose, code=code, blocks=resp.blocks))
    print(f"wrote {out} ({len(resp.blocks)} blocks, {resp.latency_ms} ms, model={resp.model})", file=sys.stderr)
    if args.print:
        print(resp.prose)
    return 0


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
        from .align import resegment

        blocks = resegment(prose, code, language, int(cfg.segment.min_block_lines))
        if blocks is None:
            print("no snapshot map and the prose cannot be paired with the code; run `prosesync gen`", file=sys.stderr)
            return 2
        base = Snapshot(prose=prose, code=code, blocks=blocks)
        store.save_snapshot(code_path, base)
        print(f"rebuilt block map ({len(blocks)} blocks) from headings/order; nothing to sync yet", file=sys.stderr)
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
        store.save_snapshot(code_path, Snapshot(prose=resp.prose, code=resp.code, blocks=resp.blocks))
    print(f"{len(resp.line_edits)} edit(s) to {resp.target_side} in {resp.latency_ms} ms (model={resp.model})", file=sys.stderr)
    return 0


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

    g = sub.add_parser("gen"); g.add_argument("file"); g.add_argument("--lang"); g.add_argument("--print", action="store_true")
    g.add_argument("--sidecar-dir", default="")
    s = sub.add_parser("sync"); s.add_argument("file"); s.add_argument("--changed", choices=["code", "prose"], required=True)
    s.add_argument("--lang"); s.add_argument("--dry-run", action="store_true"); s.add_argument("--sidecar-dir", default="")
    sub.add_parser("check-model")
    sv = sub.add_parser("serve"); sv.add_argument("--port", type=int, default=None)

    args = p.parse_args(argv)
    if args.cmd == "gen":
        return asyncio.run(cmd_gen(args))
    if args.cmd == "sync":
        return asyncio.run(cmd_sync(args))
    if args.cmd == "check-model":
        return asyncio.run(cmd_check_model(args))
    if args.cmd == "serve":
        return cmd_serve(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
