"""Bring a snapshot block map into line with the current documents and pick the affected blocks."""
from __future__ import annotations

from collections.abc import Sequence

from . import blocks as B
from .models import Block, Hunk, Side, Snapshot, other_side


class NeedsRegenerate(ValueError):
    """The block map cannot be reconciled with the documents; the prose must be regenerated."""


def realign(
    base: Snapshot, prose: str, code: str, language: str, changed: Side, other_dirty: bool, min_block_lines: int = 3
) -> tuple[list[Block], list[Hunk], list[Hunk]]:
    """Return (blocks in current coordinates, hunks on the changed side, hunks on the other side)."""
    blocks = list(base.blocks)
    bad = B.check_partition(blocks, "prose", len(B.split_lines(base.prose))) or B.check_partition(
        blocks, "code", len(B.split_lines(base.code))
    )
    if bad:
        rebuilt = resegment(base.prose, base.code, language, min_block_lines)
        if rebuilt is None:
            raise NeedsRegenerate(f"block map inconsistent ({bad}) and paragraph/unit counts differ")
        blocks = rebuilt
    other = other_side(changed)
    base_of = {"prose": base.prose, "code": base.code}
    cur_of = {"prose": prose, "code": code}
    hunks_changed = B.compute_hunks(base_of[changed], cur_of[changed])
    hunks_other = B.compute_hunks(base_of[other], cur_of[other]) if other_dirty else []
    blocks = B.shift_ranges(blocks, hunks_changed, changed)
    if hunks_other:
        blocks = B.shift_ranges(blocks, hunks_other, other)
    for side in ("prose", "code"):
        err = B.check_partition(blocks, side, len(B.split_lines(cur_of[side])))
        if err:
            raise NeedsRegenerate(f"after shifting: {err}")
    return blocks, hunks_changed, hunks_other


def resegment(prose: str, code: str, language: str, min_block_lines: int = 3) -> list[Block] | None:
    """Rebuild a block map from scratch by pairing paragraphs with code units in order."""
    pr = B.segment_prose(prose)
    cr = B.segment_code(code, language, min_block_lines)
    if len(pr) != len(cr) or not pr:
        return None
    return B.make_blocks(pr, cr)


def affected(blocks: Sequence[Block], hunks: Sequence[Hunk], side: Side, context: int) -> tuple[list[str], list[str]]:
    """(affected ids, editable ids = affected + context neighbours)."""
    core = B.affected_block_ids(blocks, hunks, side, context=0)
    editable = B.affected_block_ids(blocks, hunks, side, context=context)
    return core, editable
