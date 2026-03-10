"""REST API endpoints for the intentc editor."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_state_manager(project_path: str):
    """Helper: create a state manager scoped to the default output dir."""
    from config.config import load_config
    from state.manager import new_state_manager

    cfg = load_config(project_path)
    sm = new_state_manager(project_path)
    sm.initialize()
    sm.set_output_dir(cfg.build.default_output)
    return sm, cfg


class SpecUpdateRequest(BaseModel):
    content: str


class ValidationUpdateRequest(BaseModel):
    file_path: str
    content: str


@router.get("/dag")
def get_dag():
    """Return the full DAG structure with nodes and edges."""
    from editor.server import get_project_path
    from parser.parser import TargetRegistry
    from graph.dag import DAG
    from state.manager import new_state_manager
    from config.config import load_config

    project_path = get_project_path()

    registry = TargetRegistry(project_root=project_path)
    registry.load_targets()

    targets = registry.get_all_targets()

    dag = DAG()
    for target in targets:
        dag.add_target(target)
    dag.resolve()

    # Get status from state manager
    sm, cfg = _get_state_manager(project_path)

    nodes = []
    for target in targets:
        status = sm.get_target_status(target.name)
        nodes.append({
            "name": target.name,
            "status": status.value,
            "depends_on": target.intent.depends_on,
            "tags": target.intent.tags,
        })

    edges = []
    for target in targets:
        for dep in target.intent.depends_on:
            edges.append({"from": target.name, "to": dep})

    return {"nodes": nodes, "edges": edges}


@router.get("/targets/{name}")
def get_target(name: str):
    """Return a specific target's spec content, validation files, status, and latest build result."""
    from editor.server import get_project_path
    from parser.parser import TargetRegistry
    from state.manager import new_state_manager
    from config.config import load_config

    project_path = get_project_path()

    registry = TargetRegistry(project_root=project_path)
    registry.load_targets()

    try:
        target = registry.get_target(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Target '{name}' not found")

    # Read raw spec content from disk (including frontmatter)
    spec_content = ""
    if target.intent.file_path and os.path.isfile(target.intent.file_path):
        with open(target.intent.file_path, "r", encoding="utf-8") as f:
            spec_content = f.read()

    # Read raw validation file contents from disk
    validation_files = []
    for vf in target.validations:
        vf_content = ""
        if vf.file_path and os.path.isfile(vf.file_path):
            with open(vf.file_path, "r", encoding="utf-8") as f:
                vf_content = f.read()
        validation_files.append({
            "file_path": vf.file_path,
            "content": vf_content,
        })

    # Get status
    sm, cfg = _get_state_manager(project_path)
    status = sm.get_target_status(name)

    # Get latest build result
    latest_build = None
    try:
        result = sm.get_latest_build_result(name)
        latest_build = json.loads(result.model_dump_json())
    except FileNotFoundError:
        pass

    return {
        "name": name,
        "spec_content": spec_content,
        "spec_path": target.intent.file_path,
        "validations": validation_files,
        "status": status.value,
        "latest_build": latest_build,
    }


@router.put("/targets/{name}/spec")
def update_target_spec(name: str, body: SpecUpdateRequest):
    """Write new content to a target's .ic file."""
    from editor.server import get_project_path
    from parser.parser import TargetRegistry

    project_path = get_project_path()

    registry = TargetRegistry(project_root=project_path)
    registry.load_targets()

    try:
        target = registry.get_target(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Target '{name}' not found")

    if not target.intent.file_path:
        raise HTTPException(status_code=404, detail=f"Target '{name}' has no spec file path")

    with open(target.intent.file_path, "w", encoding="utf-8") as f:
        f.write(body.content)

    return {"status": "ok"}


@router.put("/targets/{name}/validation")
def update_target_validation(name: str, body: ValidationUpdateRequest):
    """Write new content to a target's validation file."""
    from editor.server import get_project_path
    from parser.parser import TargetRegistry

    project_path = get_project_path()

    registry = TargetRegistry(project_root=project_path)
    registry.load_targets()

    try:
        target = registry.get_target(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Target '{name}' not found")

    # Resolve the validation file path
    # body.file_path is relative (e.g., "relative/path.icv")
    # Resolve it relative to the project's intent directory
    abs_path = os.path.join(project_path, body.file_path)

    # Ensure the path is within the project
    abs_path = os.path.abspath(abs_path)
    if not abs_path.startswith(os.path.abspath(project_path)):
        raise HTTPException(status_code=400, detail="File path must be within the project directory")

    # Ensure parent directory exists
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(body.content)

    return {"status": "ok"}


@router.get("/project")
def get_project():
    """Return the project.ic raw file content and path."""
    from editor.server import get_project_path

    project_path = get_project_path()
    project_ic_path = os.path.join(project_path, "intent", "project.ic")

    if not os.path.isfile(project_ic_path):
        raise HTTPException(status_code=404, detail="project.ic not found")

    with open(project_ic_path, "r", encoding="utf-8") as f:
        content = f.read()

    return {
        "path": project_ic_path,
        "content": content,
    }


@router.get("/status")
def get_status():
    """Return build status for all targets, with latest build result if available."""
    from editor.server import get_project_path
    from parser.parser import TargetRegistry
    from state.manager import new_state_manager
    from config.config import load_config

    project_path = get_project_path()

    registry = TargetRegistry(project_root=project_path)
    registry.load_targets()

    targets = registry.get_all_targets()

    sm, cfg = _get_state_manager(project_path)

    statuses = {}
    for target in targets:
        status = sm.get_target_status(target.name)
        latest_build = None
        try:
            result = sm.get_latest_build_result(target.name)
            latest_build = json.loads(result.model_dump_json())
        except FileNotFoundError:
            pass

        statuses[target.name] = {
            "status": status.value,
            "latest_build": latest_build,
        }

    return statuses


@router.get("/targets/{name}/builds")
def get_target_builds(name: str):
    """Return build history for a specific target."""
    from editor.server import get_project_path
    from parser.parser import TargetRegistry

    project_path = get_project_path()

    registry = TargetRegistry(project_root=project_path)
    registry.load_targets()

    try:
        registry.get_target(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Target '{name}' not found")

    sm, cfg = _get_state_manager(project_path)
    results = sm.list_build_results(name)

    builds = []
    for r in reversed(results):  # newest first
        builds.append(json.loads(r.model_dump_json()))

    return {"target": name, "builds": builds}


def _broadcast_sync(msg: dict) -> None:
    """Push a message to WebSocket clients from a background thread."""
    import asyncio
    from editor.watcher import _broadcast

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_broadcast(json.dumps(msg)), loop)
    except Exception:
        pass


class _WsBroadcastHandler(logging.Handler):
    """Logging handler that broadcasts log records to WebSocket clients."""

    def __init__(self, target_name: str):
        super().__init__()
        self.target_name = target_name

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _broadcast_sync({
                "type": "build_output",
                "target": self.target_name,
                "line": self.format(record),
            })
        except Exception:
            pass


def _run_build(project_path: str, target_name: str) -> None:
    """Run a build synchronously (called from a background thread)."""
    from agent.factory import create_from_profile
    from builder.builder import Builder, BuildOptions
    from config.config import load_config, get_profile
    from git.manager import new_git_manager
    from state.manager import new_state_manager

    # Attach log handler to stream build output
    handler = _WsBroadcastHandler(target_name)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    build_logger = logging.getLogger("intentc.builder")
    build_logger.addHandler(handler)
    build_logger.setLevel(logging.DEBUG)

    cfg = load_config(project_path)
    profile = get_profile(cfg, "default")
    agent = create_from_profile(profile)

    sm = new_state_manager(project_path)
    sm.initialize()

    gm = new_git_manager()
    gm.initialize(project_path)

    builder = Builder(project_path, agent, sm, gm, cfg)
    opts = BuildOptions(target=target_name, force=True)

    try:
        builder.build(opts)
        status_msg = {"type": "build_complete", "target": target_name, "success": True}
    except Exception as e:
        status_msg = {"type": "build_complete", "target": target_name, "success": False, "error": str(e)}

    build_logger.removeHandler(handler)
    _broadcast_sync(status_msg)


@router.post("/targets/{name}/build")
def trigger_build(name: str, background_tasks: BackgroundTasks):
    """Trigger a build for a specific target in the background."""
    from editor.server import get_project_path
    from parser.parser import TargetRegistry

    project_path = get_project_path()

    registry = TargetRegistry(project_root=project_path)
    registry.load_targets()

    try:
        registry.get_target(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Target '{name}' not found")

    # Push immediate status update
    sm, cfg = _get_state_manager(project_path)

    background_tasks.add_task(_run_build, project_path, name)

    return {"status": "started", "target": name}


@router.post("/targets/{name}/clean")
def trigger_clean(name: str):
    """Clean a specific target's build state."""
    from editor.server import get_project_path
    from parser.parser import TargetRegistry
    from agent.factory import create_from_profile
    from builder.builder import Builder
    from config.config import load_config, get_profile
    from git.manager import new_git_manager
    from state.manager import new_state_manager

    project_path = get_project_path()

    registry = TargetRegistry(project_root=project_path)
    registry.load_targets()

    try:
        registry.get_target(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Target '{name}' not found")

    cfg = load_config(project_path)
    profile = get_profile(cfg, "default")
    agent = create_from_profile(profile)

    sm = new_state_manager(project_path)
    sm.initialize()

    gm = new_git_manager()
    gm.initialize(project_path)

    builder = Builder(project_path, agent, sm, gm, cfg)

    try:
        builder.clean(target=name, output_dir="")
        return {"status": "ok", "target": name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _run_validate(project_path: str, target_name: str) -> None:
    """Run validation synchronously (called from a background thread)."""
    from agent.factory import create_from_profile
    from builder.builder import Builder
    from config.config import load_config, get_profile
    from git.manager import new_git_manager
    from state.manager import new_state_manager

    handler = _WsBroadcastHandler(target_name)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    build_logger = logging.getLogger("intentc.builder")
    build_logger.addHandler(handler)
    build_logger.setLevel(logging.DEBUG)

    cfg = load_config(project_path)
    profile = get_profile(cfg, "default")
    agent = create_from_profile(profile)

    sm = new_state_manager(project_path)
    sm.initialize()

    gm = new_git_manager()
    gm.initialize(project_path)

    builder = Builder(project_path, agent, sm, gm, cfg)

    try:
        report = builder.validate(target=target_name, output_dir="")
        status_msg = {
            "type": "validate_complete",
            "target": target_name,
            "passed": report.passed,
            "failed": report.failed,
            "total": report.total,
        }
    except Exception as e:
        status_msg = {
            "type": "validate_complete",
            "target": target_name,
            "passed": 0,
            "failed": 0,
            "total": 0,
            "error": str(e),
        }

    build_logger.removeHandler(handler)
    _broadcast_sync(status_msg)


@router.post("/targets/{name}/validate")
def trigger_validate(name: str, background_tasks: BackgroundTasks):
    """Trigger validation for a specific target in the background."""
    from editor.server import get_project_path
    from parser.parser import TargetRegistry

    project_path = get_project_path()

    registry = TargetRegistry(project_root=project_path)
    registry.load_targets()

    try:
        registry.get_target(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Target '{name}' not found")

    background_tasks.add_task(_run_validate, project_path, name)

    return {"status": "started", "target": name}


@router.get("/config")
def get_config():
    """Return the raw config.yaml content."""
    from editor.server import get_project_path

    project_path = get_project_path()
    config_path = os.path.join(project_path, ".intentc", "config.yaml")

    if not os.path.isfile(config_path):
        raise HTTPException(status_code=404, detail="config.yaml not found")

    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    return {"path": config_path, "content": content}


@router.put("/config")
def update_config(body: SpecUpdateRequest):
    """Write new content to .intentc/config.yaml."""
    from editor.server import get_project_path

    project_path = get_project_path()
    config_path = os.path.join(project_path, ".intentc", "config.yaml")

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(body.content)

    return {"status": "ok"}
