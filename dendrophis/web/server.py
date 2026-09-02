"""FastAPI and WebSocket server for Dendrophis Web Observability Interface."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dendrophis.web.bridge import EventBridge

logger = logging.getLogger(__name__)

# How long to give uvicorn's graceful shutdown (should_exit) before force-cancelling.
_SHUTDOWN_TIMEOUT_S = 5.0


def create_app(bridge: EventBridge) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Dendrophis Web Observability Interface")

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def get_index() -> FileResponse:
        index_file = static_dir / "index.html"
        return FileResponse(str(index_file))

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        # Register client and get initial event state history
        initial_history = bridge.register_client(websocket)
        try:
            # Send initial history batch
            for item in initial_history:
                import json

                await websocket.send_text(json.dumps(item))

            # Keep connection open and listen for incoming messages (e.g. pings/commands)
            while True:
                data = await websocket.receive_text()
                # Optional ping handling
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            bridge.unregister_client(websocket)
        except Exception as err:
            logger.debug(f"WebSocket client disconnected with error: {err}")
            bridge.unregister_client(websocket)

    return app


class WebObservabilityServer:
    """Manages the server process lifecycle."""

    def __init__(self, bridge: EventBridge, host: str = "127.0.0.1", port: int = 9320) -> None:
        self.bridge = bridge
        self.host = host
        self.port = port
        self.app = create_app(bridge)
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the uvicorn server asynchronously in the current event loop."""
        config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        logger.info(f"🌐 Dendrophis Web Observability interface running at http://{self.host}:{self.port}")
        await self._server.serve()

    def start_background(self) -> asyncio.Task:
        """Launch the server in a background asyncio task.

        A done callback retrieves the task's result/exception so a start failure
        (e.g. the port is already in use) is logged rather than lost, even if
        stop() is never called.
        """
        self._task = asyncio.create_task(self.start())
        self._task.add_done_callback(self._on_server_done)
        return self._task

    def _on_server_done(self, task: asyncio.Task) -> None:
        """Log a finished background server task so its exception isn't lost."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("Web observability server failed: %s", exc)

    async def stop(self) -> None:
        """Shutdown the web server and wait for the background task to finish.

        Prefers uvicorn's graceful shutdown (should_exit) and awaits the task so the
        caller can rely on the server actually being stopped. If the graceful path
        stalls, a hard cancel is the fallback. The task's exception is always
        retrieved (see _on_server_done).
        """
        if self._server is not None:
            self._server.should_exit = True

        task = self._task
        self._task = None
        if task is None or task.done():
            return

        _, pending = await asyncio.wait({task}, timeout=_SHUTDOWN_TIMEOUT_S)
        if task in pending:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
