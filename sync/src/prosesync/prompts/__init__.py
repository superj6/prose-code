"""Prompt construction. Shared by the sync service, the eval harness and the training code so
that train-time and serve-time prompts are identical by construction."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..blocks import block_text, split_lines
from ..models import Block, Hunk, Side, other_side

_HERE = Path(__file__).parent


def system_prompt(kind: str, version: str) -> str:
    return (_HERE / f"{kind}_{version}.md").read_text()


def render_blocks(text: str, blocks: Sequence[Block], side: Side) -> str:
    """Render blocks as ``[bN]`` sections. Deliberately free of per-request markers so the rendering
    is byte-identical across syncs wherever the text is unchanged (prefix caching)."""
    parts = block_text(text, blocks, side)
    out = []
    for b, t in zip(blocks, parts):
        body = t.rstrip("\n")
        out.append(f"[{b.id}]\n{body}" if body else f"[{b.id}]\n(empty)")
    return "\n\n".join(out)


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
) -> list[dict[str, str]]:
    """Prompt layout is cache-oriented: everything that is stable across consecutive syncs of the
    same pair (system prompt, then the two documents in a fixed order, without per-request tags)
    comes first; everything volatile (direction, affected list, diff, instructions) comes last, so
    the provider's prefix cache covers the system prompt and every block before the first change."""
    target = other_side(changed)
    stable = [
        f"Language: {language}",
        "",
        "=== CODE ===",
        render_blocks(code, blocks, "code"),
        "",
        "=== PROSE ===",
        render_blocks(prose, blocks, "prose"),
    ]
    volatile = [
        "",
        f"=== CHANGE ({changed} side, unified diff vs the last synced state) ===",
        render_hunks(hunks),
    ]
    if other_side_dirty:
        note = (
            f"=== NOTE: the {target} side was ALSO edited by the user since the last sync (diff below). "
            f"The {changed.upper()} change is primary; keep these edits unless they contradict it. ==="
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


def build_generate_messages(*, language: str, code: str, blocks: Sequence[Block], version: str = "v1") -> list[dict[str, str]]:
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
        {"role": "system", "content": system_prompt("generate", version)},
        {"role": "user", "content": "\n".join(user)},
    ]
