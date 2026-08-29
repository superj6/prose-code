"""Segmentation and the block partition.

* ``segment_code``  : code -> list of line ranges, one per top-level unit group (tree-sitter).
* ``segment_prose`` : prose -> list of line ranges, one per blank-line-separated paragraph.
* ``compute_hunks`` : line diff of snapshot vs current text.
* ``shift_ranges``  : move a block partition from snapshot coordinates to current coordinates.

Ranges are 0-based half-open over ``split_lines(text)`` and always form a gap-free partition.
"""
from __future__ import annotations

import difflib
import re
from collections.abc import Sequence

from .models import Block, Hunk, Side

Range = tuple[int, int]

# tree-sitter node types that are "big" units: never merged into a preceding small group.
_DEFINITION_RE = re.compile(
    r"(function|class|method|impl|struct|enum|trait|interface|type|module|namespace|protocol)"
    r"_(definition|declaration|item|statement)|decorated_definition|export_statement|func_literal"
)
_COMMENT_RE = re.compile(r"comment")

_LANGUAGE_ALIASES = {
    "typescriptreact": "tsx",
    "javascriptreact": "jsx",
    "c++": "cpp",
    "shellscript": "bash",
    "py": "python",
    "ts": "typescript",
    "js": "javascript",
    "rs": "rust",
}


def split_lines(text: str) -> list[str]:
    """Lines of ``text`` without a trailing empty element for the final newline."""
    if text == "":
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def join_lines(lines: Sequence[str]) -> str:
    return "\n".join(lines) + ("\n" if lines else "")


def normalize_language(language: str) -> str:
    language = (language or "").lower()
    return _LANGUAGE_ALIASES.get(language, language)


def _get_parser(language: str):
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:  # pragma: no cover - optional at runtime
        return None
    try:
        return get_parser(normalize_language(language))
    except Exception:  # noqa: BLE001 - unknown language: fall back to paragraphs
        return None


def _units_treesitter(lines: list[str], language: str) -> list[tuple[int, int, str]] | None:
    """Top-level nodes as (start_row, end_row_inclusive, type)."""
    parser = _get_parser(language)
    if parser is None:
        return None
    tree = parser.parse(join_lines(lines).encode("utf-8"))
    root = tree.root_node
    # Some grammars wrap everything in a single program/module node; use its children.
    units = []
    for node in root.children:
        end_row = node.end_point.row
        if node.end_point.column == 0 and end_row > node.start_point.row:
            end_row -= 1  # node ends exactly at a line start: it does not own that line
        units.append((node.start_point.row, end_row, node.type))
    if not units:
        return None
    return units


def _units_paragraphs(lines: list[str]) -> list[tuple[int, int, str]]:
    units = []
    start = None
    for i, line in enumerate(lines):
        if line.strip():
            if start is None:
                start = i
        elif start is not None:
            units.append((start, i - 1, "paragraph"))
            start = None
    if start is not None:
        units.append((start, len(lines) - 1, "paragraph"))
    return units


def _is_definition(unit: tuple[int, int, str], min_block_lines: int) -> bool:
    start, end, typ = unit
    return bool(_DEFINITION_RE.search(typ)) or (end - start + 1) >= min_block_lines


def _group_units(lines: list[str], units: list[tuple[int, int, str]], min_block_lines: int) -> list[Range]:
    """Merge units into blocks; return a partition of ``range(len(lines))``."""
    starts: list[int] = []
    group_start = group_end = None
    for idx, unit in enumerate(units):
        start, end, _typ = unit
        if group_start is None:
            group_start, group_end = start, end
            continue
        adjacent = start <= group_end + 1  # no blank line in between
        prev_is_comment = bool(_COMMENT_RE.search(units[idx - 1][2])) and adjacent
        small_group = (group_end - group_start + 1) < min_block_lines
        merge = prev_is_comment or (adjacent and not _is_definition(units[idx - 1], min_block_lines)) or (
            small_group and not _is_definition(unit, min_block_lines)
        )
        if merge:
            group_end = end
        else:
            starts.append(group_start)
            group_start, group_end = start, end
    if group_start is not None:
        starts.append(group_start)
    return _partition_from_starts(starts, len(lines))


def _partition_from_starts(starts: list[int], total: int) -> list[Range]:
    """Blocks start at the given rows (first block always starts at 0) and run to the next start."""
    if total == 0:
        return []
    starts = sorted(set(starts))
    if not starts or starts[0] != 0:
        starts = [0] + [s for s in starts if s > 0]
    ranges = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else total
        if e > s:
            ranges.append((s, e))
    return ranges


CHILD_HEADER_RE = re.compile(r"^## child: (.+?)\s*$")


def segment_code(code: str, language: str, min_block_lines: int = 3) -> list[Range]:
    lines = split_lines(code)
    if not lines:
        return []
    if normalize_language(language) == "prosetree":
        # a directory's synthetic document: one unit per `## child: name` section (blank lines inside)
        starts = [i for i, ln in enumerate(lines) if CHILD_HEADER_RE.match(ln)]
        return _partition_from_starts(starts, len(lines))
    units = _units_treesitter(lines, language)
    if units:
        return _group_units(lines, units, min_block_lines)
    paragraphs = _units_paragraphs(lines)  # no grammar: paragraphs are the units, no merging
    if not paragraphs:
        return [(0, len(lines))]
    return _partition_from_starts([u[0] for u in paragraphs], len(lines))


def segment_prose(prose: str) -> list[Range]:
    lines = split_lines(prose)
    if not lines:
        return []
    units = _units_paragraphs(lines)
    if not units:
        return [(0, len(lines))]
    return _partition_from_starts([u[0] for u in units], len(lines))


SUMMARY_ID = "s"


def is_summary_paragraph(prose: str, rng: Range) -> bool:
    """A file summary paragraph starts with a level-1 heading: ``# <name>`` (blocks use ``##``)."""
    lines = split_lines(prose)
    first = next((ln for ln in lines[rng[0] : rng[1]] if ln.strip()), "")
    return first.startswith("# ") and not first.startswith("## ")


def pair_ranges(prose: str, prose_ranges: Sequence[Range], code_ranges: Sequence[Range]) -> list[Block] | None:
    """Pair paragraphs with code units in order, allowing a leading summary paragraph that pairs
    with an empty code range. None when the counts cannot be reconciled."""
    if not prose_ranges or not code_ranges:
        return None
    has_summary = is_summary_paragraph(prose, prose_ranges[0])
    body = list(prose_ranges[1:]) if has_summary else list(prose_ranges)
    if len(body) != len(code_ranges):
        return None
    blocks = make_blocks(body, code_ranges)
    if has_summary:
        blocks.insert(0, Block(id=SUMMARY_ID, prose=tuple(prose_ranges[0]), code=(0, 0)))
    return blocks


def make_blocks(prose_ranges: Sequence[Range], code_ranges: Sequence[Range], start_id: int = 1, prefix: str = "b") -> list[Block]:
    if len(prose_ranges) != len(code_ranges):
        raise ValueError(f"prose has {len(prose_ranges)} blocks but code has {len(code_ranges)}")
    return [
        Block(id=f"{prefix}{start_id + i}", prose=tuple(p), code=tuple(c))
        for i, (p, c) in enumerate(zip(prose_ranges, code_ranges))
    ]


def side_partition(text: str, side: Side, language: str = "prosetree", prefix: str = "p") -> list[Block]:
    """Free mode: a partition of ONE side (paragraphs for prose, units for code); the other side's
    ranges are empty. A leading ``# summary`` paragraph on the prose side gets id ``s``."""
    ranges = segment_prose(text) if side == "prose" else segment_code(text, language)
    blocks: list[Block] = []
    n = 1
    for i, rng in enumerate(ranges):
        if side == "prose" and i == 0 and is_summary_paragraph(text, rng):
            blocks.append(Block(id=SUMMARY_ID, prose=tuple(rng), code=(0, 0)))
            continue
        bid = f"{prefix}{n}"
        n += 1
        blocks.append(Block(id=bid, prose=tuple(rng), code=(0, 0)) if side == "prose" else Block(id=bid, prose=(0, 0), code=tuple(rng)))
    return blocks


def next_block_id(blocks: Sequence[Block], prefix: str = "b") -> int:
    best = 0
    for b in blocks:
        m = re.fullmatch(re.escape(prefix) + r"(\d+)", b.id)
        if m:
            best = max(best, int(m.group(1)))
    return best + 1


def check_partition(blocks: Sequence[Block], side: Side, total_lines: int) -> str | None:
    """Return an error message if the blocks do not partition ``range(total_lines)``."""
    if not blocks:
        return None if total_lines == 0 else "no blocks for a non-empty document"
    pos = 0
    for b in blocks:
        s, e = b.range(side)
        if s != pos:
            return f"block {b.id} {side} starts at {s}, expected {pos}"
        if e < s:
            return f"block {b.id} {side} has negative length"
        pos = e
    if pos != total_lines:
        return f"blocks end at {pos} but the {side} document has {total_lines} lines"
    return None


def block_text(text: str, blocks: Sequence[Block], side: Side) -> list[str]:
    lines = split_lines(text)
    out = []
    for b in blocks:
        s, e = b.range(side)
        out.append(join_lines(lines[s:e]))
    return out


# --------------------------------------------------------------------------------------- diffing


def compute_hunks(old: str, new: str) -> list[Hunk]:
    a, b = split_lines(old), split_lines(new)
    hunks = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        hunks.append(
            Hunk(
                old_start=i1, old_lines=i2 - i1, new_start=j1, new_lines=j2 - j1,
                old_text=join_lines(a[i1:i2]), new_text=join_lines(b[j1:j2]),
            )
        )
    return hunks


def shift_position(p: int, hunks: Sequence[Hunk]) -> int:
    """Map a block boundary from snapshot coordinates to current coordinates.

    Rules: a replaced region that straddles a boundary goes entirely to the earlier block; lines
    inserted exactly at a boundary attach to the preceding block.
    """
    delta = 0
    for h in hunks:
        hs, he = h.old_start, h.old_start + h.old_lines
        if he <= p:  # entirely before (or an insertion exactly at) the boundary
            delta += h.new_lines - h.old_lines
        elif hs < p < he:  # straddles: boundary moves to the end of the replacement
            return hs + delta + h.new_lines
        else:
            break
    return p + delta


def shift_ranges(blocks: Sequence[Block], hunks: Sequence[Hunk], side: Side) -> list[Block]:
    if not hunks or not blocks:
        return list(blocks)
    boundaries = [blocks[0].range(side)[0]] + [b.range(side)[1] for b in blocks]
    shifted = [shift_position(p, hunks) for p in boundaries]
    shifted[0] = 0
    return [b.with_range(side, (shifted[i], shifted[i + 1])) for i, b in enumerate(blocks)]


def affected_block_ids(blocks: Sequence[Block], hunks: Sequence[Hunk], side: Side, context: int = 0) -> list[str]:
    """Ids of snapshot blocks touched by the hunks, plus ``context`` neighbours on each side."""
    hit = set()
    for i, b in enumerate(blocks):
        s, e = b.range(side)
        for h in hunks:
            hs, he = h.old_start, h.old_start + h.old_lines
            if h.old_lines == 0:
                touched = s < hs <= e or (hs == s == 0)
            else:
                touched = hs < e and he > s
            if touched:
                for j in range(max(0, i - context), min(len(blocks), i + context + 1)):
                    hit.add(j)
    return [blocks[j].id for j in sorted(hit)]
