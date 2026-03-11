"""File watcher for the intentc editor - watches intent/ for changes."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from fastapi import WebSocket, WebSocketDisconnect
import json

logger = logging.getLogger(__name__)

_connections: list[WebSocket] = []
_watch_task: asyncio.Task | None = None
_intent_dir: str = ""
_event_loop: asyncio.AbstractEventLoop | None = None

async def start_watcher(intent_dir: str) -> None:
    """Start watching the intent directory for file changes."""
    global _watch_task, _intent_dir, _event_loop
    _intent_dir = intent_dir
    _event_loop = asyncio.get_running_loop()
    _watch_task = asyncio.create_task(_watch_loop(intent_dir))
    logger.info("File watcher started for %s", intent_dir)

async def stop_watcher() -> None:
    """Stop the file watcher."""
    global _watch_task
    if _watch_task:
        _watch_task.cancel()
        try:
            await _watch_task
        except asyncio.CancelledError:
            pass
        _watch_task = None
    logger.info("File watcher stopped")

async def _watch_loop(intent_dir: str) -> None:
    """Watch for file changes using watchfiles and broadcast to WebSocket clients."""
    try:
        from watchfiles import awatch, Change
        async for changes in awatch(intent_dir):
            for change_type, path in changes:
                if not (path.endswith('.ic') or path.endswith('.icv')):
                    continue
                # Convert watchfiles Change enum to string
                event_map = {Change.added: "created", Change.modified: "modified", Change.deleted: "deleted"}
                event = event_map.get(change_type, "modified")
                rel_path = str(Path(path).relative_to(Path(intent_dir).parent))
                message = json.dumps({"type": "file_changed", "path": rel_path, "event": event})
                await _broadcast(message)
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.error("File watcher error: %s", e)

async def _broadcast(message: str) -> None:
    """Send a message to all connected WebSocket clients."""
    disconnected = []
    for ws in _connections:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        _connections.remove(ws)

async def ws_changes(websocket: WebSocket) -> None:
    """WebSocket endpoint for file change notifications."""
    await websocket.accept()
    _connections.append(websocket)
    try:
        while True:
            # Keep connection alive, client doesn't send data
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _connections:
            _connections.remove(websocket)
