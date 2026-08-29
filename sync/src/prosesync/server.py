"""Local HTTP server for the VS Code extension.

    GET  /health                       -> {"ok": true, "backend": "openai", "model": "..."}
    POST /generate {code, language, code_path} -> GenerateResponse
    POST /generate_code {prose, language, code_path} -> GenerateCodeResponse   (the inverse: prose blocks -> code)
    POST /create {prose, language, code_path}  -> {prose, code, blocks}  (new prose file, summary-only allowed)
    POST /sync     SyncRequest          -> SSE stream: "preview" (Preview)* / "edit" (LineEdit) ... "done" (SyncResponse)
                                           or event "error" {"message", "needs_regenerate"}
    POST /align    {prose, code, language} -> {"blocks": [...]} or 409 when the prose is stale (regenerate)
    POST /feedback Feedback             -> {"ok": true}
    POST /tree/generate {root}          -> generate prose for every file under root + DIR.prose per directory
    POST /tree/propagate_up {code_path} -> re-sync ancestor DIR.prose files after a file changed
    POST /tree/push_down {dir}          -> apply edits made in dir/DIR.prose to its children

Cancellation: the extension aborts the HTTP request; the streaming generator is closed and the
backend stream with it. The port is printed as ``PROSESYNC_PORT=<n>`` on stdout at startup.
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from omegaconf import DictConfig

from .align import NeedsRegenerate, resegment
from .backends import get_backend
from .engine import Engine
from .models import Feedback, GenerateCodeResponse, GenerateResponse, LineEdit, Preview, SyncRequest


class GenerateBody(SyncRequest.__bases__[0]):  # BaseModel
    code: str
    language: str
    code_path: str = ""
    model: str | None = None


class AlignBody(SyncRequest.__bases__[0]):  # BaseModel
    prose: str
    code: str
    language: str
    code_path: str = ""       # when given, the committed versions (git HEAD) are preferred as the base
    prose_path: str = ""


class GenerateCodeBody(SyncRequest.__bases__[0]):  # BaseModel
    prose: str
    language: str
    code_path: str = ""
    model: str | None = None


class TreeBody(SyncRequest.__bases__[0]):  # BaseModel
    path: str
    sidecar_dir: str = ""
    overwrite: bool = False


def _sse(event: str, data: Any) -> str:
    payload = data.model_dump() if hasattr(data, "model_dump") else data
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def create_app(cfg: DictConfig, backend_name: str | None = None) -> FastAPI:
    backend = get_backend(cfg, backend_name)
    engine = Engine(cfg, backend)
    app = FastAPI(title="prosesync")
    app.state.engine = engine

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "backend": backend.name, "model": str(cfg.sync.model), "prompt_version": engine.prompt_version}

    @app.post("/generate")
    async def generate(body: GenerateBody) -> GenerateResponse:
        return await engine.generate(body.code, body.language, body.code_path, model=body.model)

    @app.post("/generate_code")
    async def generate_code(body: GenerateCodeBody) -> GenerateCodeResponse:
        return await engine.generate_code(body.prose, body.language, body.code_path, model=body.model)

    @app.post("/create")
    async def create(body: GenerateCodeBody) -> dict[str, Any]:
        """New prose file (possibly summary-only) -> code + normalised prose + block map."""
        prose, code, blocks = await engine.create_from_prose(body.prose, body.language, body.code_path)
        return {"prose": prose, "code": code, "blocks": [b.model_dump() for b in blocks]}

    @app.post("/sync")
    async def sync(req: SyncRequest, request: Request) -> StreamingResponse:
        queue: asyncio.Queue = asyncio.Queue()

        async def on_line_edit(le: LineEdit) -> None:
            await queue.put(("edit", le))

        async def on_preview(pv: Preview) -> None:
            await queue.put(("preview", pv))

        async def run() -> None:
            try:
                resp = await engine.sync(req, on_line_edit=on_line_edit, on_preview=on_preview)
                await queue.put(("done", resp))
            except NeedsRegenerate as e:
                await queue.put(("error", {"message": str(e), "needs_regenerate": True}))
            except Exception as e:  # noqa: BLE001 - report to the client
                await queue.put(("error", {"message": f"{type(e).__name__}: {e}", "needs_regenerate": False}))
            await queue.put(None)

        task = asyncio.create_task(run())

        async def stream() -> AsyncIterator[str]:
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield _sse(*item)
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/align")
    async def align(body: AlignBody) -> JSONResponse:
        """Rebuild the block map for an existing prose/code pair without calling the model. Returns
        the base texts the map belongs to (committed versions when the paths are in git)."""
        from pathlib import Path

        from . import store

        prose, code, source = body.prose, body.code, "request"
        if body.code_path and body.prose_path and Path(body.code_path).exists() and Path(body.prose_path).exists():
            prose, code, source = store.base_texts(Path(body.code_path), Path(body.prose_path))
            if source != "git":
                prose, code = body.prose, body.code
        blocks = resegment(prose, code, body.language, engine.min_block_lines)
        if blocks is None and source == "git":  # committed pair stale? try the working copy
            prose, code, source = body.prose, body.code, "request"
            blocks = resegment(prose, code, body.language, engine.min_block_lines)
        if blocks is None:
            return JSONResponse({"error": "prose and code cannot be paired; regenerate"}, status_code=409)
        return JSONResponse({"blocks": [b.model_dump() for b in blocks], "prose": prose, "code": code, "source": source})

    def _tree_json(result) -> dict[str, Any]:
        return {
            "generated": [str(p) for p in result.generated],
            "synced": [{"path": str(p), "edits": n} for p, n in result.synced],
            "unchanged": [str(p) for p in result.unchanged],
            "errors": [{"path": str(p), "error": e} for p, e in result.errors],
        }

    @app.post("/tree/generate")
    async def tree_generate(body: TreeBody) -> dict[str, Any]:
        from pathlib import Path

        from .tree import generate_tree

        return _tree_json(await generate_tree(engine, Path(body.path), body.sidecar_dir, overwrite=body.overwrite))

    @app.post("/tree/propagate_up")
    async def tree_propagate_up(body: TreeBody) -> dict[str, Any]:
        from pathlib import Path

        from .tree import propagate_up

        return _tree_json(await propagate_up(engine, Path(body.path), body.sidecar_dir))

    @app.post("/tree/push_down")
    async def tree_push_down(body: TreeBody) -> dict[str, Any]:
        from pathlib import Path

        from .tree import propagate_down

        return _tree_json(await propagate_down(engine, Path(body.path), body.sidecar_dir))

    @app.post("/feedback")
    async def feedback(fb: Feedback) -> JSONResponse:
        engine.log.write("feedback", fb.model_dump())
        return JSONResponse({"ok": True})

    return app


def _free_port(host: str) -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def serve(cfg: DictConfig, backend_name: str | None = None, port: int | None = None) -> int:
    import uvicorn

    host = str(cfg.server.host)
    port = port if port is not None else int(cfg.server.port)
    if port == 0:
        port = _free_port(host)
    print(f"PROSESYNC_PORT={port}", flush=True)
    uvicorn.run(create_app(cfg, backend_name), host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    from .cli import main

    sys.exit(main(["serve", *sys.argv[1:]]))
