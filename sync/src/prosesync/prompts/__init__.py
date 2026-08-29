"""Prompt construction. Shared by the sync service, the eval harness and the training code so
that train-time and serve-time prompts are identical by construction."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..blocks import SUMMARY_ID, block_text, split_lines
from ..models import Block, Hunk, Side, other_side

_HERE = Path(__file__).parent


def system_prompt(kind: str, version: str) -> str:
    return (_HERE / f"{kind}_{version}.md").read_text()


OUTLINE_CHARS = 80


def outline(text: str) -> str:
    """One-line summary of a collapsed block: its first non-empty line, truncated."""
    for ln in split_lines(text):
        if ln.strip():
            ln = ln.strip()
            return ln if len(ln) <= OUTLINE_CHARS else ln[: OUTLINE_CHARS - 1] + "…"
    return "(empty)"


def render_blocks(text: str, blocks: Sequence[Block], side: Side, full: Sequence[str] | None = None) -> str:
    """Render blocks as ``[bN]`` sections. Deliberately free of per-request markers so the rendering
    is byte-identical across syncs wherever the text is unchanged (prefix caching).

    With ``full`` given, only those block ids are rendered in full; the others collapse to
    ``[bN] (collapsed: <first line>)`` so long files cost tokens proportional to the edit."""
    parts = block_text(text, blocks, side)
    out = []
    for b, t in zip(blocks, parts):
        if full is not None and b.id not in full:
            out.append(f"[{b.id}] (collapsed: {outline(t)})")
            continue
        body = t.rstrip("\n")
        if b.id == SUMMARY_ID and side == "code":
            out.append(f"[{b.id}]\n(file summary: no code)")
            continue
        out.append(f"[{b.id}]\n{body}" if body else f"[{b.id}]\n(empty)")
    return "\n\n".join(out)


def window_ids(blocks: Sequence[Block], editable: Sequence[str], max_full_blocks: int, radius: int) -> list[str] | None:
    """Ids to render in full, or None to render everything (small pairs)."""
    if len(blocks) <= max_full_blocks:
        return None
    ids = [b.id for b in blocks]
    keep: set[int] = set()
    for i, bid in enumerate(ids):
        if bid in editable:
            keep.update(range(max(0, i - radius), min(len(ids), i + radius + 1)))
    keep.update({0, 1} if ids[0] == SUMMARY_ID else {0})  # summary + file head: cheap and orienting
    return [ids[i] for i in sorted(keep)]


def render_hunks(hunks: Sequence[Hunk]) -> str:
    if not hunks:
        return "(no textual change)"
    out = []
    for h in hunks:
        out.append(f"@@ -{h.old_start + 1},{h.old_lines} +{h.new_start + 1},{h.new_lines} @@")
        out.extend("-" + ln for ln in split_lines(h.old_text))
        out.extend("+" + ln for ln in split_lines(h.new_text))
    return "\n".join(out)


def build_sync_messages(
    *,
    language: str,
    prose: str,
    code: str,
    blocks: Sequence[Block],
    changed: Side,
    hunks: Sequence[Hunk],
    affected: Sequence[str],
    editable: Sequence[str],
    other_side_dirty: bool = False,
    other_hunks: Sequence[Hunk] = (),
    version: str = "v1",
    full: Sequence[str] | None = None,
    protected: Sequence[str] = (),
) -> list[dict[str, str]]:
    """Prompt layout is cache-oriented: everything that is stable across consecutive syncs of the
    same pair (system prompt, then the two documents in a fixed order, without per-request tags)
    comes first; everything volatile (direction, affected list, diff, instructions) comes last, so
    the provider's prefix cache covers the system prompt and every block before the first change."""
    target = other_side(changed)
    stable = [
        f"Language: {language}",
        *(["(prosetree: the CODE side is the list of child summaries of a directory, one `## name` paragraph per child.)"] if language == "prosetree" else []),
        "",
        "=== CODE ===",
        render_blocks(code, blocks, "code", full),
        "",
        "=== PROSE ===",
        render_blocks(prose, blocks, "prose", full),
    ]
    volatile = []
    if full is not None:
        volatile.append("Blocks shown as (collapsed: ...) are unchanged context and are not editable.")
    volatile += [
        "",
        f"=== CHANGE ({changed} side, unified diff vs the last synced state) ===",
        render_hunks(hunks),
    ]
    if other_side_dirty:
        note = (
            f"=== NOTE: the {target} side was ALSO edited by the user since the last sync (diff below). "
            f"Those edits are the user's pending intent and will be applied to the {changed.upper()} side in a "
            f"separate step. Do NOT modify, revert or 'reconcile' them - blocks {', '.join(protected) or '(none)'} "
            f"are off-limits - even where they disagree with the {changed.upper()} side right now. ==="
        )
        volatile += ["", note, render_hunks(other_hunks)]
    volatile += [
        "",
        f"The user edited the {changed.upper()} side. Produce edits to the {target.upper()} side.",
        f"Affected blocks: {', '.join(affected) or '(none)'}. Editable blocks: {', '.join(editable) or '(none)'}.",
        f'Return JSON: {{"edits": [{{"op", "block", "text", "reason"}}]}} with edits to the {target.upper()} side only.',
    ]
    return [
        {"role": "system", "content": system_prompt("system", version)},
        {"role": "user", "content": "\n".join(stable + volatile)},
    ]


def build_generate_messages(
    *, language: str, code: str, blocks: Sequence[Block], version: str = "v1", kind: str = "generate"
) -> list[dict[str, str]]:
    user = [
        f"Language: {language}",
        f"Write one paragraph for each of the {len(blocks)} blocks, in order.",
        "",
        "=== CODE ===",
        render_blocks(code, blocks, "code"),
        "",
        'Return JSON: {"paragraphs": [{"block": "b1", "prose": "..."}, ...]} with exactly one entry per block.',
    ]
    return [
        {"role": "system", "content": system_prompt(kind, version)},
        {"role": "user", "content": "\n".join(user)},
    ]


def build_generate_code_messages(
    *, language: str, prose: str, blocks: Sequence[Block], version: str = "v1", free: bool = False
) -> list[dict[str, str]]:
    body = [b for b in blocks if b.id != SUMMARY_ID]
    user = [
        f"Language: {language}",
        "Write the complete file this prose describes, one code block per top-level unit, in order."
        if free
        else f"Write the code for each of the {len(body)} numbered blocks, in order.",
        "",
        "=== PROSE ===",
        render_blocks(prose, blocks, "prose"),
        "",
        'Return JSON: {"blocks": [{"block": "b1", "code": "..."}, ...]}'
        + (" with one entry per code unit you write (block ids are yours to choose)." if free else " with one entry per numbered block."),
    ]
    return [
        {"role": "system", "content": system_prompt("generate_code_free" if free else "generate_code", version)},
        {"role": "user", "content": "\n".join(user)},
    ]


def build_free_sync_messages(
    *, language: str, prose: str, code: str, prose_blocks: Sequence[Block], code_blocks: Sequence[Block], changed: Side,
    hunks: Sequence[Hunk], affected: Sequence[str], editable: Sequence[str], version: str = "v1", unpushed: bool = False,
    full_code: Sequence[str] | None = None, notes: Sequence[str] = (),
) -> list[dict[str, str]]:
    """Free mode: each side has its own partition; annotations link paragraphs to blocks; the model
    edits the target side. ``full_code`` limits which code blocks are rendered in full."""
    target = other_side(changed)
    is_dir = language == "prosetree"
    user = [
        f"Language: {language}",
        "(free-form pair: paragraphs carry `## names` annotations naming the blocks they describe)",
        "",
        "=== CODE (one block per immediate child: `## child: name` + that child's entire prose) ===" if is_dir else "=== CODE ===",
        render_blocks(code, code_blocks, "code", full_code),
        "",
        "=== PROSE (free-form account of the directory) ===" if is_dir else "=== PROSE (free-form; `## names` lines are annotations) ===",
        render_blocks(prose, prose_blocks, "prose"),
        "",
        f"=== CHANGE ({changed} side, unified diff vs the last synced state) ===",
        render_hunks(hunks),
    ]
    if unpushed:
        user += ["", "NOTE: the PROSE side also has user edits that have not been pushed to the children yet; leave them alone."]
    if full_code is not None:
        user += ["", "Code blocks shown as (collapsed: ...) are unchanged context and are not editable."]
    for n in notes:
        user += ["", f"NOTE: {n}"]
    user += [
        "",
        f"The {changed.upper()} side changed. Produce edits to the {target.upper()} side.",
        f"Affected blocks: {', '.join(affected) or '(none)'}. Editable blocks: {', '.join(editable) or '(none)'}.",
        f'Return JSON: {{"edits": [{{"op", "block", "text", "reason"}}]}} with edits to the {target.upper()} side only.',
    ]
    return [
        {"role": "system", "content": system_prompt("system_free" if is_dir else "system_freefile", version)},
        {"role": "user", "content": "\n".join(user)},
    ]


def build_generate_free_messages(*, language: str, code: str, blocks: Sequence[Block], version: str = "v1") -> list[dict[str, str]]:
    is_dir = language == "prosetree"
    user = [
        f"Language: {language}",
        (
            f"The directory has {len(blocks)} immediate children (blocks, each `## child: name` + its whole prose)."
            if is_dir
            else f"The file has {len(blocks)} code blocks."
        )
        + " Write the summary and as many annotated paragraphs as it deserves.",
        "",
        "=== CHILDREN ===" if is_dir else "=== CODE ===",
        render_blocks(code, blocks, "code"),
        "",
        'Return JSON: {"summary": "...", "paragraphs": [{"refs": ["name", ...], "prose": "..."}, ...]}.',
    ]
    return [
        {"role": "system", "content": system_prompt("generate_dir" if is_dir else "generate_file", version)},
        {"role": "user", "content": "\n".join(user)},
    ]
