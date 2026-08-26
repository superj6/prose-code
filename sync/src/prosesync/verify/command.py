"""Run a configured shell command against the candidate code written to a temp file."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from ..models import VerifyResult

_SUFFIX = {"python": ".py", "typescript": ".ts", "tsx": ".tsx", "javascript": ".js", "go": ".go", "rust": ".rs", "java": ".java"}


class CommandVerifier:
    name = "command"

    def __init__(self, argv: list[str], timeout_s: float = 60.0):
        self.argv = argv
        self.timeout_s = timeout_s

    def check(self, language: str, code: str) -> VerifyResult | None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / f"candidate{_SUFFIX.get(language, '.txt')}"
            path.write_text(code)
            argv = [a.replace("{file}", str(path)) for a in self.argv]
            try:
                proc = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout_s, cwd=d, check=False)
            except (OSError, subprocess.TimeoutExpired) as e:
                return VerifyResult(ok=False, verifier=self.name, message=f"{argv[0]}: {e}")
            if proc.returncode == 0:
                return VerifyResult(ok=True, verifier=self.name)
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-8:]
            return VerifyResult(ok=False, verifier=self.name, message="\n".join(tail) or f"exit {proc.returncode}")
