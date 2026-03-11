"""Tests for the intentc editor module."""

from __future__ import annotations

import json
import os
import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def project_dir():
    """Create a temporary intentc project with spec files."""
    import subprocess
    d = tempfile.mkdtemp()
    # Initialize git repo
    subprocess.run(["git", "init"], cwd=d, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=d, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, capture_output=True)
    # .intentc/config.yaml
    intentc_dir = os.path.join(d, ".intentc")
    os.makedirs(intentc_dir)
    config_path = os.path.join(intentc_dir, "config.yaml")
    with open(config_path, "w") as f:
        f.write(
            "version: 1\n"
            "profiles:\n"
            "  default:\n"
            "    provider: cli\n"
            "    command: echo\n"
            "    timeout: 5m\n"
            "    retries: 1\n"
            "build:\n"
            "  default_output: build-default\n"
            "logging:\n"
            "  level: info\n"
        )
    # .intentc/state/
    os.makedirs(os.path.join(intentc_dir, "state"))

    # intent/project.ic
    intent_dir = os.path.join(d, "intent")
    os.makedirs(intent_dir)
    with open(os.path.join(intent_dir, "project.ic"), "w") as f:
        f.write(
            "---\n"
            "name: test-project\n"
            "version: 1\n"
            "tags: [test]\n"
            "---\n\n"
            "# Test Project\n\n"
            "A test project for editor tests.\n"
        )

    # intent/core/intent/intent.ic (nested)
    core_intent_dir = os.path.join(intent_dir, "core", "intent")
    os.makedirs(core_intent_dir)
    with open(os.path.join(core_intent_dir, "intent.ic"), "w") as f:
        f.write(
            "---\n"
            "name: core/intent\n"
            "version: 1\n"
            "depends_on: []\n"
            "tags: [foundation]\n"
            "---\n\n"
            "# Intent Type\n\n"
            "Core intent type.\n"
        )

    # intent/core/intent/validations.icv
    with open(os.path.join(core_intent_dir, "validations.icv"), "w") as f:
        f.write(
            "---\n"
            "target: core/intent\n"
            "version: 1\n"
            "validations:\n"
            "  - name: intent-exists\n"
            "    type: folder_check\n"
            "    path: src/core\n"
            "---\n\n"
            "# Intent Validations\n"
        )

    # intent/core/parser/parser.ic (nested, depends on core/intent)
    parser_dir = os.path.join(intent_dir, "core", "parser")
    os.makedirs(parser_dir)
    with open(os.path.join(parser_dir, "parser.ic"), "w") as f:
        f.write(
            "---\n"
            "name: core/parser\n"
            "version: 1\n"
            "depends_on: [core/intent]\n"
            "tags: [parsing]\n"
            "---\n\n"
            "# Parser\n\n"
            "Parses spec files.\n"
        )

    yield d
    shutil.rmtree(d)


@pytest.fixture
def client(project_dir):
    """Create a FastAPI test client for the editor."""
    from editor.server import create_app

    app = create_app(project_dir)
    return TestClient(app)


# ─── REST API Tests ───


class TestGetDag:
    def test_returns_nodes_and_edges(self, client):
        res = client.get("/api/dag")
        assert res.status_code == 200
        data = res.json()
        assert "nodes" in data
        assert "edges" in data

        names = [n["name"] for n in data["nodes"]]
        assert "core/intent" in names
        assert "core/parser" in names

    def test_nodes_have_status(self, client):
        res = client.get("/api/dag")
        data = res.json()
        for node in data["nodes"]:
            assert "status" in node
            assert "name" in node
            assert "depends_on" in node
            assert "tags" in node

    def test_edges_reflect_dependencies(self, client):
        res = client.get("/api/dag")
        data = res.json()
        # core/parser depends on core/intent, so edge from core/parser to core/intent
        edge = {"from": "core/parser", "to": "core/intent"}
        assert edge in data["edges"]


class TestGetDagTree:
    def test_returns_tree_structure(self, client):
        res = client.get("/api/dag")
        data = res.json()
        assert "tree" in data
        tree = data["tree"]
        assert tree["type"] == "module"
        core_mod = next(c for c in tree["children"] if c["name"] == "core")
        assert core_mod["type"] == "module"
        child_names = [c["name"] for c in core_mod["children"]]
        assert "intent" in child_names
        assert "parser" in child_names


class TestGetUpstream:
    def test_returns_upstream_deps(self, client):
        res = client.get("/api/targets/core/parser/upstream")
        assert res.status_code == 200
        data = res.json()
        assert data["target"] == "core/parser"
        assert "core/intent" in data["upstream"]

    def test_root_has_no_upstream(self, client):
        res = client.get("/api/targets/core/intent/upstream")
        assert res.status_code == 200
        data = res.json()
        assert data["upstream"] == []


class TestGetTarget:
    def test_returns_target_details(self, client):
        res = client.get("/api/targets/core/intent")
        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "core/intent"
        assert "spec_content" in data
        assert "name: core/intent" in data["spec_content"]
        assert "status" in data

    def test_returns_spec_path(self, client):
        res = client.get("/api/targets/core/intent")
        data = res.json()
        assert "spec_path" in data
        assert data["spec_path"].endswith("intent.ic")

    def test_returns_validations(self, client):
        res = client.get("/api/targets/core/intent")
        data = res.json()
        assert "validations" in data
        assert len(data["validations"]) == 1
        v = data["validations"][0]
        assert "file_path" in v
        assert "content" in v
        assert "intent-exists" in v["content"]

    def test_returns_404_for_unknown_target(self, client):
        res = client.get("/api/targets/nonexistent")
        assert res.status_code == 404


class TestUpdateSpec:
    def test_writes_spec_to_disk(self, client, project_dir):
        new_content = "---\nname: core/intent\nversion: 1\n---\n\n# Updated Core\n"
        res = client.put(
            "/api/targets/core/intent/spec",
            json={"content": new_content},
        )
        assert res.status_code == 200

        # Verify file on disk
        ic_path = os.path.join(project_dir, "intent", "core", "intent", "intent.ic")
        with open(ic_path) as f:
            assert f.read() == new_content

    def test_returns_404_for_unknown_target(self, client):
        res = client.put(
            "/api/targets/nonexistent/spec",
            json={"content": "test"},
        )
        assert res.status_code == 404


class TestUpdateValidation:
    def test_writes_validation_to_disk(self, client, project_dir):
        new_content = "---\ntarget: core/intent\nversion: 1\nvalidations: []\n---\n"
        icv_path = os.path.join(project_dir, "intent", "core", "intent", "validations.icv")
        res = client.put(
            "/api/targets/core/intent/validation",
            json={"file_path": icv_path, "content": new_content},
        )
        assert res.status_code == 200

        with open(icv_path) as f:
            assert f.read() == new_content

    def test_rejects_path_outside_project(self, client):
        res = client.put(
            "/api/targets/core/intent/validation",
            json={"file_path": "/tmp/evil.icv", "content": "hack"},
        )
        assert res.status_code == 400


class TestGetProject:
    def test_returns_project_intent(self, client):
        res = client.get("/api/project")
        assert res.status_code == 200
        data = res.json()
        assert "content" in data
        assert "name: test-project" in data["content"]
        assert "path" in data


class TestGetStatus:
    def test_returns_status_for_all_targets(self, client):
        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.json()
        assert "core/intent" in data
        assert "core/parser" in data
        assert data["core/intent"]["status"] == "pending"


class TestGetTargetBuilds:
    def test_returns_empty_builds(self, client):
        res = client.get("/api/targets/core/intent/builds")
        assert res.status_code == 200
        data = res.json()
        assert data["target"] == "core/intent"
        assert data["builds"] == []

    def test_returns_404_for_unknown_target(self, client):
        res = client.get("/api/targets/nonexistent/builds")
        assert res.status_code == 404


class TestTriggerClean:
    def test_clean_target(self, client):
        res = client.post("/api/targets/core/intent/clean")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["target"] == "core/intent"

    def test_clean_unknown_target(self, client):
        res = client.post("/api/targets/nonexistent/clean")
        assert res.status_code == 404


class TestTriggerBuild:
    def test_build_returns_started(self, client):
        res = client.post("/api/targets/core/intent/build")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "started"
        assert data["target"] == "core/intent"

    def test_build_unknown_target(self, client):
        res = client.post("/api/targets/nonexistent/build")
        assert res.status_code == 404


class TestTriggerValidate:
    def test_validate_returns_started(self, client):
        res = client.post("/api/targets/core/intent/validate")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "started"
        assert data["target"] == "core/intent"

    def test_validate_unknown_target(self, client):
        res = client.post("/api/targets/nonexistent/validate")
        assert res.status_code == 404


class TestGetConfig:
    def test_returns_config_content(self, client):
        res = client.get("/api/config")
        assert res.status_code == 200
        data = res.json()
        assert "content" in data
        assert "path" in data
        assert "profiles:" in data["content"]

    def test_returns_404_when_missing(self, project_dir):
        """Config endpoint returns 404 if config.yaml doesn't exist."""
        config_path = os.path.join(project_dir, ".intentc", "config.yaml")
        os.remove(config_path)

        from editor.server import create_app
        app = create_app(project_dir)
        c = TestClient(app)
        res = c.get("/api/config")
        assert res.status_code == 404


class TestUpdateConfig:
    def test_writes_config_to_disk(self, client, project_dir):
        new_content = "version: 2\nprofiles:\n  default:\n    provider: cli\n    command: echo\n"
        res = client.put("/api/config", json={"content": new_content})
        assert res.status_code == 200

        config_path = os.path.join(project_dir, ".intentc", "config.yaml")
        with open(config_path) as f:
            assert f.read() == new_content


# ─── WebSocket Tests ───


class TestFileChangesWebSocket:
    def test_connects_successfully(self, client):
        with client.websocket_connect("/ws/changes") as ws:
            # Connection established, just close cleanly
            pass


class TestAgentWebSocket:
    def test_connects_successfully(self, client):
        with client.websocket_connect("/ws/agent") as ws:
            pass

    def test_rejects_empty_prompt(self, client):
        with client.websocket_connect("/ws/agent") as ws:
            ws.send_text(json.dumps({"prompt": "", "target": "core/intent"}))
            response = ws.receive_text()
            data = json.loads(response)
            assert data["type"] == "error"
            assert "Empty" in data["message"]


# ─── Server Tests ───


class TestCreateApp:
    def test_creates_app(self, project_dir):
        from editor.server import create_app

        app = create_app(project_dir)
        assert app is not None
        assert app.title == "intentc editor"

    def test_get_project_path(self, project_dir):
        from editor.server import create_app, get_project_path

        create_app(project_dir)
        assert get_project_path() == os.path.abspath(project_dir)


# ─── Agent Bridge Tests ───


class TestBuildContext:
    def test_builds_context_with_target(self, project_dir):
        from editor.agent_bridge import _build_context

        parts = _build_context(project_dir, "core/intent", "hello")
        joined = "\n\n".join(parts)
        assert "<system>" in joined
        assert "<project-intent>" in joined
        assert '<feature-intent name="core/intent">' in joined
        assert "<user-prompt>" in joined
        assert "hello" in joined

    def test_builds_context_without_target(self, project_dir):
        from editor.agent_bridge import _build_context

        parts = _build_context(project_dir, "", "hello")
        joined = "\n\n".join(parts)
        assert "<system>" in joined
        assert "<user-prompt>" in joined
        # No feature-intent when no target
        assert "<feature-intent" not in joined

    def test_includes_validation_files(self, project_dir):
        from editor.agent_bridge import _build_context

        parts = _build_context(project_dir, "core/intent", "test")
        joined = "\n\n".join(parts)
        assert "<validations" in joined
        assert "intent-exists" in joined


class TestResolveAgentCommand:
    def test_resolves_cli_agent(self, project_dir):
        from editor.server import create_app
        create_app(project_dir)

        from editor.agent_bridge import _resolve_agent_command

        cmd = _resolve_agent_command(project_dir)
        assert cmd[0] == "echo"


# ─── CLI Integration Test ───


class TestEditCommand:
    def test_edit_command_exists(self):
        """Verify the edit command is registered in the CLI."""
        from cli.main import app
        from typer.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert "edit" in result.output

    def test_edit_rejects_non_project(self, tmp_path):
        """Verify edit fails on non-initialized project."""
        from typer.testing import CliRunner
        from cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["edit", str(tmp_path)])
        assert result.exit_code == 1
        assert "not an initialized intentc project" in result.output


# ─── Static Files Test ───


class TestStaticFiles:
    def test_serves_index_html(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert "intentc editor" in res.text

    def test_serves_css(self, client):
        res = client.get("/styles.css")
        assert res.status_code == 200

    def test_serves_js(self, client):
        res = client.get("/app.js")
        assert res.status_code == 200
