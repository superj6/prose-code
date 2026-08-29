"""Records -> rendered train/val/test examples, split by seed/pair so no file leaks across splits.

    .venv/bin/python ml/src/data/prepare.py --records ml/data/synth.jsonl [--records ml/data/interactions.jsonl] --out-dir outputs/data
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "sync" / "src"))
sys.path.insert(0, str(REPO))

from prosesync.config import load_config

from ml.src.data.dataset import render
from ml.src.data.records import read_jsonl


def split_key(rec: dict) -> str:
    meta = rec.get("meta") or {}
    return str(meta.get("seed") or meta.get("pair_id") or rec.get("id"))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--records", action="append", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--val-frac", type=float, default=0.05)
    p.add_argument("--test-frac", type=float, default=0.05)
    p.add_argument("--config", default=None)
    args = p.parse_args()
    cfg = load_config(args.config)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {name: (out / f"{name}.jsonl").open("w", encoding="utf-8") for name in ("train", "val", "test")}
    counts = {"train": 0, "val": 0, "test": 0, "skipped": 0}
    for path in args.records:
        for rec in read_jsonl(Path(path)):
            ex = render(rec, cfg)
            if ex is None:
                counts["skipped"] += 1
                continue
            h = int(hashlib.sha1(split_key(rec).encode()).hexdigest(), 16) % 10_000 / 10_000
            name = "test" if h < args.test_frac else "val" if h < args.test_frac + args.val_frac else "train"
            files[name].write(json.dumps(ex, ensure_ascii=False) + "\n")
            counts[name] += 1
    for f in files.values():
        f.close()
    print(json.dumps({"out": str(out), **counts}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
