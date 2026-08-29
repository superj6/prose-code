"""Paragraph annotations: the cheap map from free-form prose to the blocks it talks about.

Every prose paragraph except the ``# name`` summary starts with a ``## a, b`` line naming what it
covers - code unit names for a file, child names for a directory. That is all the locality the
sync needs: a change on one side selects the annotated counterparts on the other, and only those
are editable and sent in full.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from . import blocks as B
from .models import Block

_ANNOTATION_RE = re.compile(r"^\s*##\s+(.+?)\s*$")
_TOKEN_RE = re.compile(r"[^,;\s`]+")


def paragraph_refs(prose: str, prose_blocks: Sequence[Block]) -> dict[str, list[str]]:
    """block id -> annotation tokens (normalised); [] for the summary or an unannotated paragraph."""
    lines = B.split_lines(prose)
    out: dict[str, list[str]] = {}
    for b in prose_blocks:
        s, e = b.prose
        first = next((ln for ln in lines[s:e] if ln.strip()), "")
        m = _ANNOTATION_RE.match(first)
        out[b.id] = [_norm(t) for t in _TOKEN_RE.findall(m.group(1))] if m and b.id != B.SUMMARY_ID else []
    return out


def block_names(code: str, language: str, code_blocks: Sequence[Block]) -> dict[str, list[str]]:
    """block id -> the names it can be referred to by: every definition name in the block (any
    language with a grammar), the child name for a directory document, plus the block id itself."""
    lines = B.split_lines(code)
    out: dict[str, list[str]] = {}
    if B.normalize_language(language) == "prosetree":
        for b in code_blocks:
            s, e = b.code
            first = next((ln for ln in lines[s:e] if ln.strip()), "")
            m = B.CHILD_HEADER_RE.match(first)
            out[b.id] = [b.id] + ([_norm(m.group(1))] if m else [])
        return out
    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(B.normalize_language(language))
    except Exception:  # noqa: BLE001 - no grammar: ids only
        parser = None
    for b in code_blocks:
        s, e = b.code
        names = [b.id]
        if parser is not None:
            src = B.join_lines(lines[s:e]).encode("utf-8")
            root = parser.parse(src).root_node
            for node in root.children:
                names.extend(_norm(n) for n in _definition_names(node, src))
        out[b.id] = names
    return out


def _definition_names(node, src: bytes, depth: int = 0) -> list[str]:
    from .names import _NAMED_UNIT_RE, _first_named

    found: list[str] = []
    if _NAMED_UNIT_RE.search(node.type):
        named = _first_named(node)
        if named is not None:
            found.append(src[named.start_byte : named.end_byte].decode("utf-8", "replace"))
    if depth < 2:
        for child in node.children:
            if child.type in ("block", "class_body", "declaration_list", "body", "field_declaration_list"):
                for grandchild in child.children:
                    found.extend(_definition_names(grandchild, src, depth + 1))
    return found


def resolve(tokens: Sequence[str], names: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """(matching block ids in document order, unresolved tokens)."""
    hits: list[str] = []
    unresolved: list[str] = []
    for t in tokens:
        t = _norm(t)
        matched = [bid for bid, ns in names.items() if t in ns or any(n.endswith("." + t) or t.endswith("." + n) for n in ns)]
        if matched:
            hits.extend(m for m in matched if m not in hits)
        else:
            unresolved.append(t)
    order = list(names)
    hits.sort(key=order.index)
    return hits, unresolved


def related_paragraphs(changed_code_ids: Sequence[str], names: dict[str, list[str]], refs: dict[str, list[str]]) -> list[str]:
    """Prose block ids whose annotations name any of the changed code blocks."""
    wanted = {n for bid in changed_code_ids for n in names.get(bid, [])}
    return [pid for pid, toks in refs.items() if any(t in wanted or any(w.endswith("." + t) or t.endswith("." + w) for w in wanted) for t in toks)]


def _norm(token: str) -> str:
    token = token.strip().strip("`").strip()
    token = token.split("(")[0]
    return token.lower()


_BACKTICK_RE = re.compile(r"`([^`]+)`")


def auto_annotate(prose: str, names: dict[str, list[str]]) -> str:
    """Add a ``## names`` line to every unannotated paragraph (except the summary) whose text
    mentions resolvable identifiers in backticks. Paragraphs with no match stay unannotated."""
    lines = B.split_lines(prose)
    out: list[str] = []
    for rng in B.segment_prose(prose):
        block = lines[rng[0] : rng[1]]
        body = [ln for ln in block if ln.strip()]
        first = body[0] if body else ""
        if body and not B.is_summary_paragraph(prose, rng) and not _ANNOTATION_RE.match(first):
            tokens = []
            for ident in _BACKTICK_RE.findall("\n".join(body)):
                hits, _ = resolve([ident], names)
                if hits and _norm(ident) not in tokens:
                    tokens.append(ident.strip())
            if tokens:
                block = [f"## {', '.join(tokens)}", *block]
        out.extend(block)
    return B.join_lines(out)
