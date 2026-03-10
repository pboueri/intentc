"""Agent bridge for the intentc editor - WebSocket agent interaction."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


async def ws_agent(websocket: WebSocket) -> None:
    """WebSocket endpoint for agent chat interaction."""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            prompt = msg.get("prompt", "")
            target_name = msg.get("target", "")

            if not prompt:
                await websocket.send_text(json.dumps({"type": "error", "message": "Empty prompt"}))
                continue

            await _handle_agent_prompt(websocket, prompt, target_name)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("Agent bridge error: %s", e)


async def _handle_agent_prompt(websocket: WebSocket, prompt: str, target_name: str) -> None:
    """Process a prompt, invoke the agent, stream response back."""
    from editor.server import get_project_path

    project_path = get_project_path()

    try:
        # Load context
        context_parts = _build_context(project_path, target_name, prompt)
        full_prompt = "\n\n".join(context_parts)

        # Resolve agent command
        cmd_parts = _resolve_agent_command(project_path)

        # Stream agent output
        process = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Send prompt to stdin
        process.stdin.write(full_prompt.encode())
        await process.stdin.drain()
        process.stdin.close()

        # Stream stdout
        while True:
            chunk = await process.stdout.read(256)
            if not chunk:
                break
            await websocket.send_text(json.dumps({
                "type": "chunk",
                "content": chunk.decode(errors="replace")
            }))

        await process.wait()

        if process.returncode != 0:
            stderr = await process.stderr.read()
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": f"Agent exited with code {process.returncode}: {stderr.decode(errors='replace')}"
            }))
        else:
            await websocket.send_text(json.dumps({"type": "done"}))

    except Exception as e:
        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))


def _build_context(project_path: str, target_name: str, user_prompt: str) -> list[str]:
    """Build the prompt context with spec and validation info."""
    parts = []

    # System prompt
    parts.append(
        "<system>\nYou are an AI coding assistant working on an intentc project. "
        "Help the user with their request in the context of the selected target's "
        "spec and validations.\n</system>"
    )

    # Project intent
    project_ic = os.path.join(project_path, "intent", "project.ic")
    if os.path.isfile(project_ic):
        with open(project_ic) as f:
            content = f.read()
        parts.append(f"<project-intent>\n{content}\n</project-intent>")

    # Target spec and validations
    if target_name:
        target_dir = os.path.join(project_path, "intent", target_name)
        if os.path.isdir(target_dir):
            # Spec file
            ic_path = os.path.join(target_dir, f"{target_name}.ic")
            if os.path.isfile(ic_path):
                with open(ic_path) as f:
                    content = f.read()
                parts.append(f"<feature-intent name=\"{target_name}\">\n{content}\n</feature-intent>")

            # Validation files
            for fname in sorted(os.listdir(target_dir)):
                if fname.endswith(".icv"):
                    icv_path = os.path.join(target_dir, fname)
                    with open(icv_path) as f:
                        content = f.read()
                    parts.append(f"<validations file=\"{fname}\">\n{content}\n</validations>")

    # User prompt
    parts.append(f"<user-prompt>\n{user_prompt}\n</user-prompt>")

    return parts


def _resolve_agent_command(project_path: str) -> list[str]:
    """Resolve the agent CLI command from project config."""
    from config.config import load_config, get_profile

    cfg = load_config(project_path)
    profile = get_profile(cfg, "default")

    if profile.provider == "claude":
        cmd = ["claude", "-p", "--output-format", "text"]
        if profile.model_id:
            cmd.extend(["--model", profile.model_id])
        return cmd
    elif profile.provider == "cli":
        if not profile.command:
            raise ValueError("CLI agent profile has no command configured")
        return [profile.command, *profile.cli_args]
    else:
        # Fallback
        return ["claude", "-p", "--output-format", "text"]
