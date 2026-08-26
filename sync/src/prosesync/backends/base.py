from __future__ import annotations

from omegaconf import DictConfig

from ..models import SyncBackend


def get_backend(cfg: DictConfig, name: str | None = None) -> SyncBackend:
    name = name or cfg.sync.get("backend", "openai")
    if name == "openai":
        from .openai_backend import OpenAIBackend

        return OpenAIBackend(cfg)
    if name == "mock":
        from .mock_backend import MockBackend

        return MockBackend()
    raise ValueError(f"unknown backend {name!r}")
