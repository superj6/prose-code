"""Data model shared by the server, the CLI, the extension (as JSON) and the training code.

Line ranges are 0-based, half-open ``[start, end)`` over ``text.split("\\n")`` with a trailing
newline stripped (see ``blocks.split_lines``). Blocks are ordered and partition BOTH documents:
every line of the prose file and every line of the code file belongs to exactly one block.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Side = Literal["prose", "code"]
EditOp = Literal["replace", "delete"]
PairMode = Literal["paired", "free"]  # paired: block N of prose <-> block N of code; free: independent partitions


def other_side(side: Side) -> Side:
    return "code" if side == "prose" else "prose"


class Block(BaseModel):
    id: str
    prose: tuple[int, int]
    code: tuple[int, int]

    def range(self, side: Side) -> tuple[int, int]:
        return self.prose if side == "prose" else self.code

    def with_range(self, side: Side, rng: tuple[int, int]) -> Block:
        return self.model_copy(update={side: rng})


class Snapshot(BaseModel):
    """The last state the model saw/produced. Hunks are computed against it."""

    prose: str
    code: str
    blocks: list[Block]
    code_blocks: list[Block] = Field(default_factory=list)  # free mode only: the code side\'s own partition


class Pair(BaseModel):
    pair_id: str
    language: str
    code_path: str
    prose: str
    code: str
    prose_version: int = 0
    code_version: int = 0
    mode: PairMode = "paired"


class Hunk(BaseModel):
    """One changed region, in the coordinates of the snapshot (old) and the current text (new)."""

    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    old_text: str = ""
    new_text: str = ""


class Change(BaseModel):
    side: Side
    cursor_line: int | None = None


class Edit(BaseModel):
    """What the model emits: block-level ops only, never line numbers.

    ``text`` is the new text of the block on the TARGET side (the side being synchronised).
    """

    op: EditOp
    block: str
    text: str | None = None
    reason: str | None = None


class Preview(BaseModel):
    """Partial text of an edit still being generated (display only, never applied)."""

    side: Side
    block: str
    start: int
    text: str
    done: bool = False


class LineEdit(BaseModel):
    """What the editor applies. Coordinates are relative to the target document *as it is after
    the previously streamed line edits of the same response* (so they can be applied in order)."""

    side: Side
    start: int
    end: int
    new_text: str
    block: str
    reason: str | None = None


class SyncOptions(BaseModel):
    verify: bool | None = None
    model: str | None = None
    max_edits: int | None = None
    broad: bool = False  # every block is editable (summary-driven / push-down syncs)


class SyncRequest(BaseModel):
    request_id: str
    pair: Pair
    base: Snapshot
    change: Change
    other_side_dirty: bool = False
    options: SyncOptions = Field(default_factory=SyncOptions)


class VerifyResult(BaseModel):
    ok: bool
    verifier: str
    message: str | None = None
    line: int | None = None


class SyncResponse(BaseModel):
    request_id: str
    base_prose_version: int
    base_code_version: int
    target_side: Side
    line_edits: list[LineEdit]
    prose: str
    code: str
    blocks: list[Block]
    code_blocks: list[Block] = Field(default_factory=list)
    verification: VerifyResult | None = None
    latency_ms: int = 0
    model: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    prose: str
    blocks: list[Block]
    code_blocks: list[Block] = Field(default_factory=list)
    latency_ms: int = 0
    model: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)


class GenerateCodeResponse(BaseModel):
    code: str
    prose: str  # the prose, normalised (one paragraph per block)
    blocks: list[Block]
    latency_ms: int = 0
    model: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)


class Feedback(BaseModel):
    sync_id: str
    outcome: Literal["accepted", "modified", "reverted"]
    dwell_s: float
    final_text_by_block: dict[str, str] = Field(default_factory=dict)


class BackendResult(BaseModel):
    """Raw model output plus accounting."""

    raw: str
    model: str
    usage: dict[str, Any] = Field(default_factory=dict)


EditCallback = Callable[[Edit], Awaitable[None]]


class SyncBackend(Protocol):
    """A backend turns a rendered prompt into a JSON object matching ``schema``.

    ``on_object`` is called with each completed top-level array element as soon as it has been
    streamed (see ``streamjson``), so the engine can validate/apply edits one at a time.
    ``on_partial(block_id_or_None, text_so_far)`` is called while an element's ``text`` streams.
    """

    name: str

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        schema_name: str,
        on_object: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        model: str | None = None,
    ) -> BackendResult: ...
