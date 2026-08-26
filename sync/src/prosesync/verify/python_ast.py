from __future__ import annotations

import ast

from ..models import VerifyResult


class PythonAstVerifier:
    name = "python_ast"

    def check(self, language: str, code: str) -> VerifyResult | None:
        if language not in ("python", "py"):
            return None
        try:
            ast.parse(code)
        except SyntaxError as e:
            line = (e.lineno or 1) - 1
            return VerifyResult(ok=False, verifier=self.name, message=f"{e.msg} (line {e.lineno})", line=line)
        return VerifyResult(ok=True, verifier=self.name)
