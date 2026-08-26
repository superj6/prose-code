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


def render_blocks(text: str, blocks: Sequence[Block], side: Side, affected: Sequence[str] = ()) -> str:
    parts = block_text(text, blocks, side)
    out = []
    for b, t in zip(blocks, parts):
        tag = f"[{b.id} AFFECTED]" if b.id in affected else f"[{b.id}]"
        body = t.rstrip("\n")
        out.append(f"{tag}\n{body}" if body else f"{tag}\n(empty)")
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
    target = other_side(changed)
    user = [
        f"Language: {language}",
        f"The user edited the {changed.upper()} side. Produce edits to the {target.upper()} side.",
        f"Editable blocks: {', '.join(editable) or '(none)'}. Affected: {', '.join(affected) or '(none)'}.",
        "",
        "=== PROSE ===",
        render_blocks(prose, blocks, "prose", affected),
        "",
        "=== CODE ===",
        render_blocks(code, blocks, "code", affected),
        "",
        f"=== CHANGE ({changed} side, unified diff vs the last synced state) ===",
        render_hunks(hunks),
    ]
    if other_side_dirty:
        note = (
            f"=== NOTE: the {target} side was ALSO edited by the user since the last sync (diff below). "
            f"The {changed.upper()} change is primary; keep these edits unless they contradict it. ==="
        )
        user += ["", note, render_hunks(other_hunks)]
    user += ["", f'Return JSON: {{"edits": [{{"op", "block", "text", "reason"}}]}} with edits to the {target.upper()} side only.']
    return [
        {"role": "system", "content": system_prompt("system", version)},
        {"role": "user", "content": "\n".join(user)},
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
