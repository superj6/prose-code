"""Local HTTP server for the VS Code extension.

    GET  /health                       -> {"ok": true, "backend": "openai", "model": "..."}
    POST /generate {code, language, code_path} -> GenerateResponse
    POST /sync     SyncRequest          -> SSE stream: "preview" (Preview)* / "edit" (LineEdit) ... "done" (SyncResponse)
                                           or event "error" {"message", "needs_regenerate"}
    POST /feedback Feedback             -> {"ok": true}

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

from .align import NeedsRegenerate
from .backends import get_backend
from .engine import Engine
from .models import Feedback, GenerateResponse, LineEdit, Preview, SyncRequest


class GenerateBody(SyncRequest.__bases__[0]):  # BaseModel
    code: str
    language: str
    code_path: str = ""
    model: str | None = None


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
