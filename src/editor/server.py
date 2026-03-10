"""Editor server for intentc - browser-based project IDE."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# Global state shared across the app
_project_path: str = ""

def get_project_path() -> str:
    return _project_path

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start file watcher on startup, stop on shutdown."""
    from editor.watcher import start_watcher, stop_watcher
    await start_watcher(os.path.join(_project_path, "intent"))
    yield
    await stop_watcher()

def create_app(project_path: str) -> FastAPI:
    """Create the FastAPI app configured for the given project."""
    global _project_path
    _project_path = os.path.abspath(project_path)

    app = FastAPI(title="intentc editor", lifespan=lifespan)

    from editor.api import router
    app.include_router(router, prefix="/api")

    from editor.watcher import ws_changes
    app.add_websocket_route("/ws/changes", ws_changes)

    from editor.agent_bridge import ws_agent
    app.add_websocket_route("/ws/agent", ws_agent)

    # Mount static files last (catch-all)
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app

def start_server(project_path: str, port: int = 8080) -> None:
    """Start the editor server. Blocks until stopped."""
    import uvicorn
    app = create_app(project_path)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
