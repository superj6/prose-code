"""Shared helpers for the training entrypoints (mirrors latent-memory/src/training/common.py)."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

import numpy as np
import torch

REPO = Path(__file__).resolve().parents[3]
for p in (REPO / "sync" / "src", REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from prosesync.config import load_config  # noqa: F401


def log_event(**payload: Any) -> None:
    print(json.dumps(payload, default=str), flush=True)


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def resolve_device(name: str | None) -> torch.device:
    if not name:
        name = "cuda" if torch.cuda.is_available() else "cpu"
    if name.startswith("cuda") and not torch.cuda.is_available():
        name = "cpu"
    return torch.device(name)
