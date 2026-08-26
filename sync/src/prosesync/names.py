"""Symbol names for code units and heading names for prose paragraphs — used to re-pair the two
sides when the block map is missing or inconsistent (external edits, formatters, git)."""
from __future__ import annotations

import re
from collections.abc import Sequence

from .blocks import Range, join_lines, split_lines

_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*?)\s*$")
_NAMED_UNIT_RE = re.compile(
    r"(function|class|method|impl|struct|enum|trait|interface|type|module|namespace|protocol|lexical|variable|const)"
    r"_(definition|declaration|item|statement|spec)|decorated_definition|export_statement"
)
_NAME_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _first_named(node, depth: int = 0):
    """First node (pre-order, shallow first) that has a `name` field."""
    if depth > 4:
        return None
    name = node.child_by_field_name("name")
    if name is not None:
        return name
    for child in node.children:
        found = _first_named(child, depth + 1)
        if found is not None:
            return found
    return None


def code_unit_names(code: str, language: str, ranges: Sequence[Range]) -> list[str | None]:
    """Symbol name of the first definition in each code range (None when there is none)."""
    try:
        from tree_sitter_language_pack import get_parser

        from .blocks import normalize_language

        parser = get_parser(normalize_language(language))
    except Exception:  # noqa: BLE001 - no grammar: no names
        return [None] * len(ranges)
    lines = split_lines(code)
    out: list[str | None] = []
    for s, e in ranges:
        src = join_lines(lines[s:e]).encode("utf-8")
        root = parser.parse(src).root_node
        name = None
        for child in root.children:
            if not _NAMED_UNIT_RE.search(child.type):
                continue  # imports, assignments, expressions: not a named definition
            named = _first_named(child)
            if named is not None:
                name = src[named.start_byte : named.end_byte].decode("utf-8", "replace")
                break
        out.append(_normalize(name))
    return out


def prose_heading_names(prose: str, ranges: Sequence[Range]) -> list[str | None]:
    r"""Name from a leading `## name` / `## class Name` / `## \`pkg.name\`` heading, else None."""
    lines = split_lines(prose)
    out: list[str | None] = []
    for s, e in ranges:
        first = next((ln for ln in lines[s:e] if ln.strip()), "")
        m = _HEADING_RE.match(first)
        if not m:
            out.append(None)
            continue
        text = m.group(1).replace("`", "")
        text = text.split("(")[0].strip()
        tokens = _NAME_TOKEN_RE.findall(text.split(".")[-1] if "." in text else text)
        out.append(_normalize(tokens[-1]) if tokens else None)
    return out


def _normalize(name: str | None) -> str | None:
    return name.strip().lower() if name else None


def names_conflict(prose_names: Sequence[str | None], code_names: Sequence[str | None]) -> int | None:
    """Index of the first position where both sides carry a name and the names differ, else None.

    Blocks must appear in the same order on both sides, so names can validate an order-based
    pairing (and detect stale prose) but never reorder it."""
    for i, (p, c) in enumerate(zip(prose_names, code_names)):
        if p is not None and c is not None and p != c:
            return i
    return None
