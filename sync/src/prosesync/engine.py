"""The sync engine: realign -> prompt -> backend -> validate/apply edits one at a time -> log."""
from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
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
    GenerateCodeResponse,
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
from .prompts import (
    build_free_sync_messages,
    build_generate_code_messages,
    build_generate_free_messages,
    build_generate_messages,
    build_sync_messages,
    window_ids,
)
from .prompts.schema import CODE_BLOCKS_SCHEMA, EDITS_SCHEMA, FREE_PROSE_SCHEMA, PARAGRAPHS_SCHEMA
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
    async def generate(
        self, code: str, language: str, code_path: str = "", model: str | None = None, kind: str = "generate", title: str | None = None
    ) -> GenerateResponse:
        t0 = time.time()
        code_ranges = B.segment_code(code, language, self.min_block_lines)
        if not code_ranges:
            return GenerateResponse(prose="", blocks=[], model=self.backend.name)
        blocks = B.make_blocks([(i, i + 1) for i in range(len(code_ranges))], code_ranges)
        messages = build_generate_messages(language=language, code=code, blocks=blocks, version=self.prompt_version, kind=kind)
        result = await self.backend.complete_json(
            messages, PARAGRAPHS_SCHEMA, "paragraphs", model=model, cache_key=f"prosesync:{pair_id_for(code_path)}"
        )
        import json

        parsed = json.loads(result.raw)
        by_block = {p["block"]: p["prose"] for p in parsed["paragraphs"]}
        title = title or (Path(code_path).name if code_path else "file")
        summary_lines = [ln.rstrip() for ln in str(parsed.get("summary") or "").strip().split("\n") if ln.strip()]
        paragraphs = [f"# {title}\n" + "\n".join(summary_lines)] if summary_lines else []
        for b in blocks:
            text = by_block.get(b.id) or f"(no description for {b.id})"
            lines = [ln.rstrip() for ln in text.strip().split("\n") if ln.strip()]  # no blank lines inside
            paragraphs.append("\n".join(lines))
        prose = "\n\n".join(paragraphs) + "\n"
        final_blocks = B.pair_ranges(prose, B.segment_prose(prose), code_ranges)
        if final_blocks is None:  # should not happen: one paragraph per block was enforced above
            raise RuntimeError("generated prose does not pair with the code blocks")
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

    # ------------------------------------------------------------------ free-form (directory) generation
    async def generate_free(self, code: str, language: str, code_path: str = "", title: str = "", model: str | None = None) -> GenerateResponse:
        """Free-form prose for a synthetic child-summary document: summary + any number of paragraphs."""
        t0 = time.time()
        code_blocks = B.side_partition(code, "code", language, prefix="b")
        if not code_blocks:
            return GenerateResponse(prose="", blocks=[], model=self.backend.name)
        messages = build_generate_free_messages(language=language, code=code, blocks=code_blocks, version=self.prompt_version)
        result = await self.backend.complete_json(
            messages, FREE_PROSE_SCHEMA, "free_prose", model=model, cache_key=f"prosesync:{pair_id_for(code_path)}"
        )
        import json

        parsed = json.loads(result.raw)

        def clean(text: str) -> str:
            return "\n".join(ln.rstrip() for ln in str(text).strip().split("\n") if ln.strip())

        paragraphs = [f"# {title or Path(code_path).name}\n{clean(parsed.get('summary') or '(no summary)')}"]
        paragraphs += [clean(pg) for pg in parsed.get("paragraphs", []) if clean(pg)]
        prose = "\n\n".join(paragraphs) + "\n"
        blocks = B.side_partition(prose, "prose", prefix="p")
        resp = GenerateResponse(prose=prose, blocks=blocks, code_blocks=code_blocks, latency_ms=int((time.time() - t0) * 1000), model=result.model, usage=result.usage)
        self.log.write("generate_free", {"pair_id": pair_id_for(code_path), "language": language, "prompt_version": self.prompt_version,
                                         "model": result.model, "code": code, "prose": prose, "blocks": blocks, "code_blocks": code_blocks,
                                         "raw": result.raw, "latency_ms": resp.latency_ms, "usage": result.usage})
        return resp

    # ------------------------------------------------------------------ generate code from prose
    async def generate_code(self, prose: str, language: str, code_path: str = "", model: str | None = None) -> GenerateCodeResponse:
        """The inverse of generate: prose paragraphs -> one code block each. The map uses the
        model's own block boundaries, so it partitions both sides by construction."""
        t0 = time.time()
        prose_ranges = B.segment_prose(prose)
        if not prose_ranges:
            return GenerateCodeResponse(code="", prose=prose, blocks=[], model=self.backend.name)
        has_summary = B.is_summary_paragraph(prose, prose_ranges[0])
        body = prose_ranges[1:] if has_summary else prose_ranges
        if not body:
            # Summary only (e.g. a child named in DIR.prose): write the whole file as one block.
            # Callers that need paragraphs regenerate them from the code (see create_from_prose).
            has_summary, body = False, prose_ranges
        blocks = B.make_blocks(body, [(i, i + 1) for i in range(len(body))])
        if has_summary:
            blocks.insert(0, Block(id=B.SUMMARY_ID, prose=tuple(prose_ranges[0]), code=(0, 0)))
        messages = build_generate_code_messages(language=language, prose=prose, blocks=blocks, version=self.prompt_version)
        result = await self.backend.complete_json(
            messages, CODE_BLOCKS_SCHEMA, "code_blocks", model=model, cache_key=f"prosesync:{pair_id_for(code_path)}"
        )
        import json

        by_block = {b["block"]: b["code"] for b in json.loads(result.raw)["blocks"]}
        code_parts, code_ranges, pos = [], [], 0
        body_blocks = [b for b in blocks if b.id != B.SUMMARY_ID]
        for i, b in enumerate(body_blocks):
            text = (by_block.get(b.id) or "").replace("\r\n", "\n").strip("\n")
            lines = text.split("\n") if text else ["pass" if language == "python" else ""]
            if i < len(body_blocks) - 1:
                lines += ["", ""] if language == "python" else [""]
            code_parts.append(B.join_lines(lines))
            code_ranges.append((pos, pos + len(lines)))
            pos += len(lines)
        code = "".join(code_parts)
        final = [b.with_range("code", code_ranges[i]) for i, b in enumerate(body_blocks)]
        if has_summary:
            final.insert(0, blocks[0])
        resp = GenerateCodeResponse(code=code, prose=prose, blocks=final, latency_ms=int((time.time() - t0) * 1000), model=result.model, usage=result.usage)
        self.log.write("generate_code", {"pair_id": pair_id_for(code_path), "language": language, "prompt_version": self.prompt_version,
                                         "model": result.model, "prose": prose, "code": code, "blocks": final, "raw": result.raw,
                                         "latency_ms": resp.latency_ms, "usage": result.usage})
        return resp

    async def create_from_prose(self, prose: str, language: str, code_path: str = "") -> tuple[str, str, list[Block]]:
        """Prose (possibly a bare ``# name`` summary) -> (prose, code, blocks) for a brand-new file.
        With only a summary, the code is written from it and the paragraphs are then derived from
        the code, keeping the author's summary."""
        ranges = B.segment_prose(prose)
        summary_only = len(ranges) == 1 and B.is_summary_paragraph(prose, ranges[0])
        gen_code = await self.generate_code(prose, language, code_path)
        if not summary_only:
            return gen_code.prose, gen_code.code, gen_code.blocks
        heading = B.split_lines(prose)[ranges[0][0]].strip()[2:].strip()
        summary_body = "\n".join(ln for ln in B.split_lines(prose)[ranges[0][0] + 1 : ranges[0][1]] if ln.strip())
        gen = await self.generate(gen_code.code, language, code_path)
        paragraphs = [B.join_lines(B.split_lines(gen.prose)[b.prose[0] : b.prose[1]]).strip("\n") for b in gen.blocks if b.id != B.SUMMARY_ID]
        final_prose = "\n\n".join([f"# {heading}\n{summary_body}", *paragraphs]) + "\n"
        code_ranges = [b.code for b in gen.blocks if b.id != B.SUMMARY_ID]
        blocks = B.pair_ranges(final_prose, B.segment_prose(final_prose), code_ranges)
        if blocks is None:
            raise RuntimeError("regenerated prose does not pair with the generated code")
        return final_prose, gen_code.code, blocks

    # ------------------------------------------------------------------ sync
    async def sync(
        self, req: SyncRequest, on_line_edit: LineEditCallback | None = None, on_preview: PreviewCallback | None = None
    ) -> SyncResponse:
        t0 = time.time()
        pair, changed = req.pair, req.change.side
        target = other_side(changed)
        if pair.mode == "free":
            return await self._sync_free(req, on_line_edit, on_preview, t0)
        blocks, hunks, other_hunks, base_blocks = realign(
            req.base, pair.prose, pair.code, pair.language, changed, req.other_side_dirty, self.min_block_lines
        )
        context = int(self.cfg.sync.context_blocks)
        core, editable = affected(base_blocks, hunks, changed, context)
        ids = [b.id for b in blocks]
        if req.options.broad:
            editable = list(ids)
        if B.SUMMARY_ID in ids:
            # The summary has no code: never a code-side target; always refreshable on the prose side.
            editable = [b for b in editable if b != B.SUMMARY_ID] if target == "code" else (
                editable if B.SUMMARY_ID in editable else [B.SUMMARY_ID, *editable]
            )
        protected: list[str] = []
        if req.other_side_dirty and other_hunks:
            # Blocks the user edited on the target side are theirs: pass 1 must not touch them.
            protected, _ = affected(base_blocks, other_hunks, target, 0)
            editable = [b for b in editable if b not in protected]
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
            version=self.prompt_version, full=full, protected=protected,
        )
        result = await self.backend.complete_json(
            messages, EDITS_SCHEMA, "edits", on_object=on_object, model=req.options.model, on_partial=on_partial,
            cache_key=f"prosesync:{pair.pair_id}",
        )
        result_holder["raw"] = result.raw
        if req.other_side_dirty and other_hunks:
            # Pass 2: the user's edits on the target side are now the primary change, applied to the
            # other side on top of pass 1's result. Both intents land; neither side is reverted.
            blocks2 = state.blocks()
            core2, editable2 = affected(blocks2, other_hunks, target, context)
            protected2 = [b for b in core if b in [x.id for x in blocks2]]
            editable2 = [b for b in editable2 if b not in protected2]
            full2 = window_ids(blocks2, editable2, int(self.cfg.window.max_full_blocks), int(self.cfg.window.radius))
            messages2 = build_sync_messages(
                language=pair.language, prose=state.text("prose"), code=state.text("code"), blocks=blocks2,
                changed=target, hunks=other_hunks, affected=core2, editable=editable2, version=self.prompt_version,
                full=full2, protected=protected2,
            )
            applied2: list[Edit] = []

            async def on_object2(obj: dict[str, Any]) -> None:
                try:
                    edit = Edit.model_validate(obj)
                except Exception as e:  # noqa: BLE001
                    warnings.append(f"pass 2: invalid edit skipped: {e}")
                    return
                if edit.block not in editable2 or len(applied2) >= max_edits:
                    warnings.append(f"pass 2: edit to {edit.block} rejected")
                    return
                try:
                    le = state.apply(edit, changed)
                except ApplyError as e:
                    warnings.append(f"pass 2: edit to {edit.block} rejected: {e}")
                    return
                applied2.append(edit)
                if le is not None:
                    line_edits.append(le)
                    if on_line_edit is not None:
                        await on_line_edit(le)

            result2 = await self.backend.complete_json(
                messages2, EDITS_SCHEMA, "edits", on_object=on_object2, model=req.options.model,
                cache_key=f"prosesync:{pair.pair_id}",
            )
            applied.extend(applied2)
            result = BackendResult(raw=result.raw + "\n" + result2.raw, model=result.model, usage=_merge_usage(result.usage, result2.usage))
            warnings.append(f"pass 2 ({target} -> {changed}): {len(applied2)} edit(s)")
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

    async def _sync_free(self, req: SyncRequest, on_line_edit, on_preview, t0: float) -> SyncResponse:
        """Free mode: the prose and code sides have independent partitions (``blocks`` = prose,
        ``code_blocks`` = code). The changed side's hunks pick the affected blocks; every block of
        the target side is editable; edits apply to the target side alone (single-sided splits)."""
        pair, changed = req.pair, req.change.side
        target = other_side(changed)
        language = pair.language
        prose_blocks = list(req.base.blocks)
        code_blocks = list(req.base.code_blocks)
        if not prose_blocks or B.check_partition(prose_blocks, "prose", len(B.split_lines(req.base.prose))):
            prose_blocks = B.side_partition(req.base.prose, "prose", prefix="p")
        if not code_blocks or B.check_partition(code_blocks, "code", len(B.split_lines(req.base.code))):
            code_blocks = B.side_partition(req.base.code, "code", language, prefix="b")
        hunks_prose = B.compute_hunks(req.base.prose, pair.prose)
        hunks_code = B.compute_hunks(req.base.code, pair.code)
        base_src = code_blocks if changed == "code" else prose_blocks
        affected_ids = B.affected_block_ids(base_src, hunks_code if changed == "code" else hunks_prose, changed, context=0)
        prose_blocks = B.shift_ranges(prose_blocks, hunks_prose, "prose")
        code_blocks = B.shift_ranges(code_blocks, hunks_code, "code")
        if B.check_partition(prose_blocks, "prose", len(B.split_lines(pair.prose))):
            prose_blocks = B.side_partition(pair.prose, "prose", prefix="p")
        if B.check_partition(code_blocks, "code", len(B.split_lines(pair.code))):
            code_blocks = B.side_partition(pair.code, "code", language, prefix="b")
        hunks = hunks_code if changed == "code" else hunks_prose
        tgt_blocks = prose_blocks if changed == "code" else code_blocks
        editable = [b.id for b in tgt_blocks]
        warnings: list[str] = []
        state = DocState(pair.prose, pair.code, tgt_blocks, language, self.min_block_lines)
        line_edits: list[LineEdit] = []
        applied: list[Edit] = []
        max_edits = req.options.max_edits or int(self.cfg.sync.max_edits)
        unpushed = bool(req.other_side_dirty and (hunks_prose if changed == "code" else hunks_code))
        if unpushed:
            warnings.append("prose side has unpushed edits" if changed == "code" else "code side changed too; not reconciled")

        def finish(model: str, usage: dict, warns: list[str]) -> SyncResponse:
            resp = self._response(req, target, state, line_edits, t0, model, usage, warns)
            if target == "prose":
                resp.blocks, resp.code_blocks, resp.code = state.blocks(), code_blocks, pair.code
            else:
                resp.blocks, resp.code_blocks, resp.prose = prose_blocks, state.blocks(), pair.prose
            return resp

        if not hunks:
            return finish(self.backend.name, {}, ["no change vs snapshot"])

        async def on_object(obj: dict[str, Any]) -> None:
            try:
                edit = Edit.model_validate(obj)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"invalid edit skipped: {e}")
                return
            if len(applied) >= max_edits or edit.block not in editable:
                warnings.append(f"edit to {edit.block} rejected")
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

        messages = build_free_sync_messages(
            language=language, prose=pair.prose, code=pair.code, prose_blocks=prose_blocks, code_blocks=code_blocks, changed=changed,
            hunks=hunks, affected=affected_ids, editable=editable, version=self.prompt_version, unpushed=unpushed,
        )
        result = await self.backend.complete_json(
            messages, EDITS_SCHEMA, "edits", on_object=on_object, model=req.options.model, cache_key=f"prosesync:{pair.pair_id}"
        )
        resp = finish(result.model, result.usage, warnings)
        self.log.write("sync", {
            "sync_id": req.request_id, "pair_id": pair.pair_id, "language": language, "mode": "free", "prompt_version": self.prompt_version,
            "model": result.model, "changed_side": changed, "prose_before": pair.prose, "code_before": pair.code, "hunks": hunks,
            "affected": affected_ids, "editable": editable, "raw": result.raw, "edits_applied": applied, "line_edits": line_edits,
            "prose_after": resp.prose, "code_after": resp.code, "blocks_after": resp.blocks, "code_blocks_after": resp.code_blocks,
            "warnings": warnings, "latency_ms": resp.latency_ms, "usage": result.usage,
        })
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
