"""Config loading: configs/base.yaml is the single source of truth; other configs `extends` it.

Same semantics as latent-memory's ``load_config`` so the ml/ half can share configs.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from omegaconf import DictConfig, OmegaConf

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "base.yaml"


def load_config(path: str | None = None, overrides: Sequence[str] | None = None) -> DictConfig:
    """Load a YAML config, following ``extends`` chains; apply ``key=value`` overrides last."""
    config_path = Path(path or os.environ.get("PROSESYNC_CONFIG") or DEFAULT_CONFIG).resolve()
    cfg = OmegaConf.load(config_path)
    if cfg.get("extends"):
        candidate = (config_path.parent / cfg.extends).resolve()
        if not candidate.exists():
            candidate = (REPO_ROOT / cfg.extends).resolve()
        cfg = OmegaConf.merge(load_config(str(candidate)), cfg)
    if overrides:
        cfg = OmegaConf.merge(cfg, OmegaConf.from_dotlist(list(overrides)))
    return cfg
