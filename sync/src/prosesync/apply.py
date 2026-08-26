"""Apply model edits (block ops) to the target side and maintain the partition.

The source side's text never changes during a sync; only its block ranges may be re-cut when a
block is deleted (its leftover lines merge into a neighbour) or split.
"""
from __future__ import annotations

from collections.abc import Sequence

from . import blocks as B
from .models import Block, Edit, LineEdit, Side, other_side


class ApplyError(ValueError):
    pass


def _trailing_blank_count(text: str) -> int:
    lines = B.split_lines(text)
    n = 0
    for line in reversed(lines):
        if line.strip():
            break
        n += 1
    return n


def _strip_trailing_blank(text: str) -> str:
    lines = B.split_lines(text)
    while lines and not lines[-1].strip():
        lines.pop()
    return B.join_lines(lines)


def _sanitize_block_text(side: Side, new_text: str, old_text: str, is_last: bool) -> str:
    """Normalise a replacement so the partition stays clean and formatting is preserved."""
    new_text = new_text.replace("\r\n", "\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    body = _strip_trailing_blank(new_text)
    trailing = _trailing_blank_count(old_text)
    if side == "prose" and not is_last:
        trailing = max(trailing, 1)
    return body + "\n" * trailing


class DocState:
    """Both documents as per-block text lists; can be re-materialised at any time."""

    def __init__(self, prose: str, code: str, blocks: Sequence[Block], language: str, min_block_lines: int = 3):
        self.language = language
        self.min_block_lines = min_block_lines
        self.ids: list[str] = [b.id for b in blocks]
        self.prose_parts: list[str] = B.block_text(prose, blocks, "prose")
        self.code_parts: list[str] = B.block_text(code, blocks, "code")
        self.next_id = B.next_block_id(blocks)

    # -- accessors -------------------------------------------------------------------------
    def parts(self, side: Side) -> list[str]:
        return self.prose_parts if side == "prose" else self.code_parts

    def text(self, side: Side) -> str:
        return "".join(self.parts(side))

    def blocks(self) -> list[Block]:
        out, p, c = [], 0, 0
        for i, bid in enumerate(self.ids):
            pl = len(B.split_lines(self.prose_parts[i]))
            cl = len(B.split_lines(self.code_parts[i]))
            out.append(Block(id=bid, prose=(p, p + pl), code=(c, c + cl)))
            p += pl
            c += cl
        return out

    def line_range(self, side: Side, index: int) -> tuple[int, int]:
        start = sum(len(B.split_lines(t)) for t in self.parts(side)[:index])
        return start, start + len(B.split_lines(self.parts(side)[index]))

    def index_of(self, block_id: str) -> int:
        try:
            return self.ids.index(block_id)
        except ValueError:
            raise ApplyError(f"unknown block id {block_id!r}") from None

    # -- edits -----------------------------------------------------------------------------
    def apply(self, edit: Edit, target: Side) -> LineEdit | None:
        """Apply one edit to ``target``; return the LineEdit the editor should perform."""
        i = self.index_of(edit.block)
        parts = self.parts(target)
        start, end = self.line_range(target, i)
        if edit.op == "delete" or (edit.op == "replace" and not (edit.text or "").strip()):
            if len(self.ids) == 1:
                raise ApplyError("cannot delete the only block")
            self._delete(i, target)
            return LineEdit(side=target, start=start, end=end, new_text="", block=edit.block, reason=edit.reason)
        if edit.text is None:
            raise ApplyError("replace edit without text")
        is_last = i == len(self.ids) - 1
        new_text = _sanitize_block_text(target, edit.text, parts[i], is_last)
        if new_text == parts[i]:
            return None
        parts[i] = new_text
        self._maybe_split(i)
        return LineEdit(side=target, start=start, end=end, new_text=new_text, block=edit.block, reason=edit.reason)

    def _delete(self, i: int, target: Side) -> None:
        """Remove block ``i``. Its text on ``target`` is dropped; leftover lines on the other side
        (usually blank lines) merge into the previous block, or the next one if it is the first."""
        src_parts = self.parts(other_side(target))
        leftover = src_parts[i]
        if i > 0:
            src_parts[i - 1] = src_parts[i - 1] + leftover
        else:
            src_parts[1] = leftover + src_parts[1]
        del self.ids[i]
        del self.prose_parts[i]
        del self.code_parts[i]

    def _maybe_split(self, i: int) -> None:
        """If block ``i`` now segments into k>1 units on BOTH sides, split it into k blocks."""
        prose_ranges = B.segment_prose(self.prose_parts[i])
        code_ranges = B.segment_code(self.code_parts[i], self.language, self.min_block_lines)
        k = len(prose_ranges)
        if k < 2 or k != len(code_ranges):
            return
        prose_lines = B.split_lines(self.prose_parts[i])
        code_lines = B.split_lines(self.code_parts[i])
        new_prose = [B.join_lines(prose_lines[s:e]) for s, e in prose_ranges]
        new_code = [B.join_lines(code_lines[s:e]) for s, e in code_ranges]
        new_ids = [self.ids[i]] + [f"b{self.next_id + n}" for n in range(k - 1)]
        self.next_id += k - 1
        self.ids[i : i + 1] = new_ids
        self.prose_parts[i : i + 1] = new_prose
        self.code_parts[i : i + 1] = new_code


def apply_edits(
    prose: str, code: str, blocks: Sequence[Block], edits: Sequence[Edit], target: Side, language: str,
    min_block_lines: int = 3,
) -> tuple[str, str, list[Block], list[LineEdit]]:
    """Convenience wrapper: apply all edits, return (prose, code, blocks, line_edits)."""
    state = DocState(prose, code, blocks, language, min_block_lines)
    line_edits = []
    for e in edits:
        le = state.apply(e, target)
        if le is not None:
            line_edits.append(le)
    return state.text("prose"), state.text("code"), state.blocks(), line_edits


__all__ = ["ApplyError", "DocState", "apply_edits", "other_side"]
