"""The sync engine: realign -> prompt -> backend -> validate/apply edits one at a time -> log."""
from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from omegaconf import DictConfig

from . import blocks as B
from .align import NeedsRegenerate, affected, realign
from .apply import ApplyError, DocState
from .interaction_log import InteractionLog
from .models import (
    BackendResult,
    Block,
    Edit,
    GenerateResponse,
    LineEdit,
    Pair,
    Preview,
    Snapshot,
    SyncBackend,
    SyncRequest,
    SyncResponse,
    other_side,
)
from .prompts import build_generate_messages, build_sync_messages, window_ids
from .prompts.schema import EDITS_SCHEMA, PARAGRAPHS_SCHEMA
from .verify import get_verifiers, run_verifiers

LineEditCallback = Callable[[LineEdit], Awaitable[None]]
PreviewCallback = Callable[[Preview], Awaitable[None]]


def pair_id_for(path: str) -> str:
    return hashlib.sha1(path.encode()).hexdigest()[:12]


class Engine:
    def __init__(self, cfg: DictConfig, backend: SyncBackend, log: InteractionLog | None = None):
        self.cfg = cfg
        self.backend = backend
        self.log = log or InteractionLog(cfg.log.get("dir"), bool(cfg.log.get("enabled", True)))
        self.min_block_lines = int(cfg.segment.min_block_lines)
        self.prompt_version = str(cfg.sync.prompt_version)

    # ------------------------------------------------------------------ generate
    async def generate(self, code: str, language: str, code_path: str = "", model: str | None = None) -> GenerateResponse:
        t0 = time.time()
        code_ranges = B.segment_code(code, language, self.min_block_lines)
        if not code_ranges:
            return GenerateResponse(prose="", blocks=[], model=self.backend.name)
        blocks = B.make_blocks([(i, i + 1) for i in range(len(code_ranges))], code_ranges)
        messages = build_generate_messages(language=language, code=code, blocks=blocks, version=self.prompt_version)
        result = await self.backend.complete_json(
            messages, PARAGRAPHS_SCHEMA, "paragraphs", model=model, cache_key=f"prosesync:{pair_id_for(code_path)}"
        )
        import json

        by_block = {p["block"]: p["prose"] for p in json.loads(result.raw)["paragraphs"]}
        paragraphs = []
        for b in blocks:
            text = by_block.get(b.id) or f"(no description for {b.id})"
            lines = [ln.rstrip() for ln in text.strip().split("\n") if ln.strip()]  # no blank lines inside
            paragraphs.append("\n".join(lines))
        prose = "\n\n".join(paragraphs) + "\n"
        prose_ranges = B.segment_prose(prose)
        final_blocks = B.make_blocks(prose_ranges, code_ranges)
        resp = GenerateResponse(
            prose=prose, blocks=final_blocks, latency_ms=int((time.time() - t0) * 1000), model=result.model, usage=result.usage
        )
        self.log.write(
            "generate",
            {
                "pair_id": pair_id_for(code_path), "language": language, "prompt_version": self.prompt_version,
                "model": result.model, "code": code, "prose": prose, "blocks": final_blocks, "raw": result.raw,
                "latency_ms": resp.latency_ms, "usage": result.usage,
            },
        )
        return resp

    # ------------------------------------------------------------------ sync
    async def sync(
        self, req: SyncRequest, on_line_edit: LineEditCallback | None = None, on_preview: PreviewCallback | None = None
    ) -> SyncResponse:
        t0 = time.time()
        pair, changed = req.pair, req.change.side
        target = other_side(changed)
        blocks, hunks, other_hunks = realign(
            req.base, pair.prose, pair.code, pair.language, changed, req.other_side_dirty, self.min_block_lines
        )
        context = int(self.cfg.sync.context_blocks)
        core, editable = affected(blocks, hunks, changed, context)
        if req.other_side_dirty:
            core2, editable2 = affected(blocks, other_hunks, target, context)
            core = sorted(set(core) | set(core2), key=lambda i: [b.id for b in blocks].index(i))
            editable = sorted(set(editable) | set(editable2), key=lambda i: [b.id for b in blocks].index(i))
        warnings: list[str] = []
        state = DocState(pair.prose, pair.code, blocks, pair.language, self.min_block_lines)
        line_edits: list[LineEdit] = []
        applied: list[Edit] = []
        max_edits = req.options.max_edits or int(self.cfg.sync.max_edits)
        result_holder: dict[str, Any] = {}

        if not hunks and not other_hunks:
            resp = self._response(req, target, state, line_edits, t0, self.backend.name, {}, ["no change vs snapshot"])
            return resp

        async def on_object(obj: dict[str, Any]) -> None:
            try:
                edit = Edit.model_validate(obj)
            except Exception as e:  # noqa: BLE001 - invalid element is skipped, not fatal
                warnings.append(f"invalid edit skipped: {e}")
                return
            if len(applied) >= max_edits:
                warnings.append(f"edit to {edit.block} dropped: max_edits={max_edits} reached")
                return
            if edit.block not in editable:
                warnings.append(f"edit to {edit.block} rejected: not in editable set {editable}")
                return
            try:
                le = state.apply(edit, target)
            except ApplyError as e:
                warnings.append(f"edit to {edit.block} rejected: {e}")
                return
            applied.append(edit)
            if le is not None:
                line_edits.append(le)
                if on_line_edit is not None:
                    await on_line_edit(le)

        preview_state = {"last": 0.0, "block": None}
        interval = float(self.cfg.sync.get("preview_interval_ms", 150)) / 1000.0

        async def on_partial(block: str | None, text: str) -> None:
            if on_preview is None or not self.cfg.sync.get("preview", True) or not block or block not in state.ids:
                return
            now = time.time()
            if now - preview_state["last"] < interval and block == preview_state["block"]:
                return
            preview_state["last"], preview_state["block"] = now, block
            start, _ = state.line_range(target, state.index_of(block))
            await on_preview(Preview(side=target, block=block, start=start, text=text))

        full = window_ids(blocks, editable, int(self.cfg.window.max_full_blocks), int(self.cfg.window.radius))
        messages = build_sync_messages(
            language=pair.language, prose=pair.prose, code=pair.code, blocks=blocks, changed=changed, hunks=hunks,
            affected=core, editable=editable, other_side_dirty=req.other_side_dirty, other_hunks=other_hunks,
            version=self.prompt_version, full=full,
        )
        result = await self.backend.complete_json(
            messages, EDITS_SCHEMA, "edits", on_object=on_object, model=req.options.model, on_partial=on_partial,
            cache_key=f"prosesync:{pair.pair_id}",
        )
        result_holder["raw"] = result.raw
        verification = None
        verify_on = req.options.verify if req.options.verify is not None else bool(self.cfg.verify.get("enabled", False))
        if verify_on and target == "code" and applied:
            verifiers = get_verifiers(self.cfg, pair.language)
            verification = run_verifiers(verifiers, pair.language, state.text("code"))
            rounds = int(self.cfg.verify.get("repair_rounds", 1))
            attempt = 0
            while not verification.ok and attempt < rounds:
                attempt += 1
                repaired, rresult = await self._repair(messages, result.raw, verification, req, blocks, target, editable, max_edits)
                if repaired is None:
                    warnings.append(f"repair round {attempt}: no usable edits")
                    break
                rverification = run_verifiers(verifiers, pair.language, repaired.text("code"))
                warnings.append(f"repair round {attempt}: {'ok' if rverification.ok else rverification.message}")
                result = BackendResult(raw=rresult.raw, model=rresult.model, usage=_merge_usage(result.usage, rresult.usage))
                if rverification.ok:
                    # Replace the whole target document with the repaired text (rare path; one edit).
                    old_lines = len(B.split_lines(state.text("code")))
                    state = repaired
                    le = LineEdit(side="code", start=0, end=old_lines, new_text=state.text("code"), block="*",
                                  reason="verification repair")
                    line_edits.append(le)
                    if on_line_edit is not None:
                        await on_line_edit(le)
                    verification = rverification
                    break
        resp = self._response(req, target, state, line_edits, t0, result.model, result.usage, warnings)
        resp.verification = verification
        self.log.write(
            "sync",
            {
                "sync_id": req.request_id, "pair_id": pair.pair_id, "language": pair.language,
                "prompt_version": self.prompt_version, "model": result.model, "changed_side": changed,
                "other_side_dirty": req.other_side_dirty, "prose_before": pair.prose, "code_before": pair.code,
                "blocks_before": blocks, "hunks": hunks, "other_hunks": other_hunks, "affected": core, "editable": editable,
                "window": full, "raw": result.raw, "edits_applied": applied, "line_edits": line_edits, "prose_after": resp.prose,
                "code_after": resp.code, "blocks_after": resp.blocks, "warnings": warnings,
                "latency_ms": resp.latency_ms, "usage": result.usage,
            },
        )
        return resp

    async def _repair(self, messages, raw, verification, req, blocks, target, editable, max_edits):
        """Ask the model for a corrected edit set, applied to a fresh copy of the original documents."""
        pair = req.pair
        follow_up = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    f"Applying your edits produced code that fails verification ({verification.verifier}): "
                    f"{verification.message}\n"
                    "Return a corrected, complete set of edits relative to the ORIGINAL documents above "
                    f"(editable blocks: {', '.join(editable)}). Same JSON format."
                ),
            },
        ]
        fresh = DocState(pair.prose, pair.code, blocks, pair.language, self.min_block_lines)
        count = 0

        async def on_object(obj):
            nonlocal count
            try:
                edit = Edit.model_validate(obj)
            except Exception:  # noqa: BLE001 - invalid element is skipped
                return
            if edit.block not in editable or count >= max_edits:
                return
            try:
                if fresh.apply(edit, target) is not None:
                    count += 1
            except ApplyError:
                return

        rresult = await self.backend.complete_json(
            follow_up, EDITS_SCHEMA, "edits", on_object=on_object, model=req.options.model,
            cache_key=f"prosesync:{pair.pair_id}",
        )
        return (fresh if count else None), rresult

    def _response(self, req, target, state, line_edits, t0, model, usage, warnings) -> SyncResponse:
        return SyncResponse(
            request_id=req.request_id, base_prose_version=req.pair.prose_version, base_code_version=req.pair.code_version,
            target_side=target, line_edits=line_edits, prose=state.text("prose"), code=state.text("code"),
            blocks=state.blocks(), latency_ms=int((time.time() - t0) * 1000), model=model, usage=usage, warnings=warnings,
        )


def _merge_usage(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, (int, float)) and isinstance(out.get(k), (int, float)):
            out[k] = out[k] + v
        elif k not in out:
            out[k] = v
    return out


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


__all__ = ["Block", "Engine", "NeedsRegenerate", "Pair", "Snapshot", "new_request_id", "pair_id_for"]
