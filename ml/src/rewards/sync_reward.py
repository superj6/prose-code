"""Automatic scorers for a sync result. Used by the eval harness now and as RL/DPO rewards later.

All scorers take the state *before* the model acted (texts + blocks + which blocks were editable)
and the state *after*, and return floats in [0, 1].
"""
from __future__ import annotations

import difflib
from collections.abc import Sequence

from prosesync import blocks as B
from prosesync.models import Block, Side
from prosesync.verify.treesitter import first_error


def syntax_ok(code: str, language: str) -> bool | None:
    """True/False from tree-sitter (ERROR/MISSING nodes); None if no grammar is available."""
    res = first_error(language, code)
    return None if res is None else res[0]


def collateral_edit_rate(
    before: str, after: str, blocks_before: Sequence[Block], blocks_after: Sequence[Block], side: Side, editable: Sequence[str]
) -> float:
    """Fraction of NON-editable blocks whose text changed. 0 = the model only touched what it may."""
    tb = dict(zip([b.id for b in blocks_before], B.block_text(before, blocks_before, side)))
    ta = dict(zip([b.id for b in blocks_after], B.block_text(after, blocks_after, side)))
    untouched = [bid for bid in tb if bid not in editable]
    if not untouched:
        return 0.0
    changed = sum(1 for bid in untouched if ta.get(bid) != tb[bid])
    return changed / len(untouched)


def similarity(a: str, b: str) -> float:
    """Whitespace-normalised line similarity in [0, 1]."""
    la = [ln.strip() for ln in B.split_lines(a) if ln.strip()]
    lb = [ln.strip() for ln in B.split_lines(b) if ln.strip()]
    return difflib.SequenceMatcher(a=la, b=lb, autojunk=False).ratio()


def score(
    *, language: str, target: Side, before: str, after: str, expected: str | None, blocks_before: Sequence[Block],
    blocks_after: Sequence[Block], editable: Sequence[str], warnings: Sequence[str],
    expected_contains: Sequence[str] = (),
) -> dict[str, float]:
    out: dict[str, float] = {}
    out["schema_valid"] = 0.0 if any("invalid" in w or "rejected" in w for w in warnings) else 1.0
    if target == "code":
        ok = syntax_ok(after, language)
        if ok is not None:
            out["syntax_valid"] = float(ok)
    out["collateral"] = collateral_edit_rate(before, after, blocks_before, blocks_after, target, editable)
    out["changed"] = float(after != before)
    if expected is not None:
        out["similarity"] = similarity(after, expected)
        out["exact"] = float(after.strip() == expected.strip())
    if expected_contains:
        out["contains"] = sum(1 for s in expected_contains if s in after) / len(expected_contains)
    return out


def reward(scores: dict[str, float]) -> float:
    """Scalar reward for preference data: gate on validity, then weighted quality terms."""
    if scores.get("schema_valid", 1.0) < 1.0:
        return 0.0
    r = 0.35 * scores.get("syntax_valid", 1.0) + 0.25 * (1.0 - scores.get("collateral", 0.0))
    r += 0.40 * scores.get("similarity", scores.get("contains", scores.get("changed", 0.0)))
    return r


__all__ = ["collateral_edit_rate", "reward", "score", "similarity", "syntax_ok"]
