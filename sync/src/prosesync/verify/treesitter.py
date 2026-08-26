"""Syntax check for any language with a tree-sitter grammar: fails on ERROR / MISSING nodes."""
from __future__ import annotations

from ..blocks import normalize_language
from ..models import VerifyResult


def first_error(language: str, code: str) -> tuple[bool, int | None] | None:
    """(ok, first error line) or None when no grammar is available."""
    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(normalize_language(language))
    except Exception:  # noqa: BLE001 - unknown language / missing grammar
        return None
    tree = parser.parse(code.encode("utf-8"))
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            return False, node.start_point.row
        stack.extend(reversed(node.children))
    return True, None


class TreeSitterVerifier:
    name = "treesitter"

    def check(self, language: str, code: str) -> VerifyResult | None:
        res = first_error(language, code)
        if res is None:
            return None
        ok, line = res
        msg = None if ok else f"syntax error near line {line + 1}"
        return VerifyResult(ok=ok, verifier=self.name, message=msg, line=line)
