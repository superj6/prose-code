"""Pluggable code verifiers, run on the code side after edits are applied (off by default).

    verify:
      enabled: true
      repair_rounds: 1
      commands:
        typescript: ["npx", "tsc", "--noEmit", "{file}"]
        python: ["python", "-m", "pyflakes", "{file}"]
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from omegaconf import DictConfig

from ..blocks import normalize_language
from ..models import VerifyResult


class Verifier(Protocol):
    name: str

    def check(self, language: str, code: str) -> VerifyResult | None:
        """None = not applicable for this language (skipped)."""


def get_verifiers(cfg: DictConfig, language: str) -> list[Verifier]:
    from .command import CommandVerifier
    from .python_ast import PythonAstVerifier
    from .treesitter import TreeSitterVerifier

    language = normalize_language(language)
    verifiers: list[Verifier] = [TreeSitterVerifier(), PythonAstVerifier()]
    commands = cfg.verify.get("commands") or {}
    if language in commands:
        verifiers.append(CommandVerifier(list(commands[language]), timeout_s=float(cfg.verify.get("command_timeout_s", 60))))
    return verifiers


def run_verifiers(verifiers: Sequence[Verifier], language: str, code: str) -> VerifyResult:
    """First failure wins; success reports the verifiers that actually ran."""
    ran = []
    for v in verifiers:
        result = v.check(language, code)
        if result is None:
            continue
        ran.append(v.name)
        if not result.ok:
            return result
    return VerifyResult(ok=True, verifier="+".join(ran) or "none")
