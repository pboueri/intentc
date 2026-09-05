"""Agents: the pluggable code-generation backends of intentc.

Contains every agent-facing type (contexts, responses, profile, prompt templates),
the Agent interface, the CLIAgent / ClaudeAgent / MockAgent implementations, the
``create_from_profile`` factory, and prompt rendering helpers.
"""

from __future__ import annotations

import abc
import json
import os
import subprocess
from importlib import resources
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from intentc.core.models import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Validation,
    ValidationFile,
)

LogFn = Callable[[str], None]


def _noop_log(_message: str) -> None:
    return None


class AgentError(Exception):
    """Raised when an agent invocation fails."""


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------


class PromptTemplates(BaseModel):
    build: str = ""
    validate_template: str = ""
    plan: str = ""
    difference: str = ""
    init: str = ""


_AGENT_PROMPT_FILES = {
    "build": "build.prompt",
    "validate_template": "validate.prompt",
    "plan": "plan.prompt",
    "init": "init.prompt",
}


def _read_resource(package: str, name: str) -> str:
    try:
        return (resources.files(package) / "prompts" / name).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, TypeError, ModuleNotFoundError):
        return ""


def load_default_prompts() -> PromptTemplates:
    """Load the prompt templates bundled with the installed package."""
    fields = {key: _read_resource("intentc.build.agents", fname) for key, fname in _AGENT_PROMPT_FILES.items()}
    fields["difference"] = _read_resource("intentc.differencing", "difference.prompt")
    return PromptTemplates(**fields)


def render_validation_text(validation: Validation) -> str:
    """Readable block for a single validation entry (used in prompts)."""
    lines = [f"- name: {validation.name}", f"  type: {validation.type}", f"  severity: {validation.severity.value}"]
    for key, value in validation.args.items():
        if isinstance(value, str) and "\n" in value:
            lines.append(f"  {key}: |")
            lines.extend(f"    {line}" for line in value.rstrip().splitlines())
        else:
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def render_validations_text(validations: list[ValidationFile]) -> str:
    blocks = [render_validation_text(v) for vf in validations for v in vf.validations]
    return "\n\n".join(blocks) if blocks else "(no validations defined)"


def _previous_errors_text(errors: list[str]) -> str:
    if not errors:
        return ""
    bullets = "\n".join(f"- {e}" for e in errors)
    return (
        "\n### Previous attempts failed\n"
        "Earlier attempts at this feature failed for the reasons below. Fix them this time:\n"
        f"{bullets}\n"
    )


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _format(template: str, **values: str) -> str:
    return template.format_map(_SafeDict(values))


def render_prompt(template: str, ctx: BuildContext, validation: Validation | None = None) -> str:
    """Render a build/validate/plan template with values from a BuildContext."""
    deps = "\n".join(f"- {d}" for d in ctx.dependency_names) if ctx.dependency_names else "(none)"
    single = render_validation_text(validation) if validation is not None else render_validations_text(ctx.validations)
    return _format(
        template,
        project=ctx.project_intent.body if ctx.project_intent else "",
        implementation=ctx.implementation.body if ctx.implementation else "",
        feature=ctx.intent.body if ctx.intent else "",
        feature_name=ctx.feature_path or (ctx.intent.name if ctx.intent else ""),
        output_dir=ctx.output_dir,
        dependencies=deps,
        validations=render_validations_text(ctx.validations),
        validation=single,
        response_file=ctx.response_file_path,
        previous_errors=_previous_errors_text(ctx.previous_errors),
        seed_prompt=ctx.seed_prompt,
    )


def render_differencing_prompt(template: str, ctx: DifferencingContext) -> str:
    return _format(
        template,
        project=ctx.project_intent.body if ctx.project_intent else "",
        implementation=ctx.implementation.body if ctx.implementation else "",
        output_dir_a=ctx.output_dir_a,
        output_dir_b=ctx.output_dir_b,
        response_file=ctx.response_file_path,
    )


SPECIFICATIONS_SUMMARY = """### `.ic` intent files

YAML frontmatter between `---` lines, then a Markdown body describing WHAT to build:

```
---
name: feature_name          # required, matches the directory name
depends_on: [module/other]  # optional, feature paths relative to intent/ (wildcards like core/* allowed)
tags: [optional]
---

# Feature title

What the feature does, its inputs/outputs, and how it fits the project.
```

`intent/project.ic` is the same format without `depends_on`. `intent/implementations/default.ic`
describes HOW to build (language, framework, packaging, conventions).

### `.icv` validation files

Pure YAML (no `---`). One per feature directory, checked after every build:

```yaml
target: module/feature
version: 1
validations:
  - name: tests-pass
    type: command_validation        # deterministic: the command must exit 0
    args:
      command: "pytest {output_dir} -q"
      cwd: "."
  - name: entrypoint-exists
    type: file_exists               # deterministic: every path/glob must exist
    args:
      paths: ["app/main.py"]
  - name: behaves-as-specified
    type: agent_validation          # an agent judges the rubric
    severity: warning               # error (default) blocks the build, warning is advisory
    args:
      rubric: >
        Precisely describe what must be true for this validation to pass.
```

### Layout

```
intent/
  project.ic
  implementations/default.ic
  {module}/{feature}/{feature}.ic
  {module}/{feature}/validation.icv
```

A feature is a directory containing a `.ic` file; its path relative to `intent/` is its identifier.
"""


def render_init_prompt(template: str, project_name: str, user_prompt: str | None = None) -> str:
    section = ""
    if user_prompt:
        section = (
            "The user described the project below. Generate the full project structure "
            "directly from it without asking questions.\n\n"
            f"{user_prompt}"
        )
    return _format(template, project_name=project_name, specifications=SPECIFICATIONS_SUMMARY, user_prompt=section)


# ---------------------------------------------------------------------------
# Profile, contexts, responses
# ---------------------------------------------------------------------------


class AgentProfile(BaseModel):
    """Named, reusable agent configuration."""

    name: str
    provider: str
    command: str = ""
    cli_args: list[str] = Field(default_factory=list)
    timeout: float = 3600.0
    retries: int = 3
    model_id: str | None = None
    effort: str | None = None
    prompt_templates: PromptTemplates | None = None
    sandbox_write_paths: list[str] = Field(default_factory=list)
    sandbox_read_paths: list[str] = Field(default_factory=list)


class BuildContext(BaseModel):
    """Everything an agent needs to act on a target."""

    intent: IntentFile
    validations: list[ValidationFile] = Field(default_factory=list)
    output_dir: str
    generation_id: str
    dependency_names: list[str] = Field(default_factory=list)
    project_intent: ProjectIntent
    implementation: Implementation | None = None
    response_file_path: str
    previous_errors: list[str] = Field(default_factory=list)
    seed_prompt: str = ""
    feature_path: str = ""


class DifferencingContext(BaseModel):
    output_dir_a: str
    output_dir_b: str
    project_intent: ProjectIntent
    response_file_path: str
    implementation: Implementation | None = None


class BuildResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    summary: str = ""
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)


class ValidationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    status: str
    reason: str = ""
    severity: str = "error"
    type: str = "agent_validation"
    duration_secs: float = 0.0


class DimensionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    status: str
    rationale: str = ""


class DifferencingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    dimensions: list[DimensionResult] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Agent interface
# ---------------------------------------------------------------------------


class Agent(abc.ABC):
    """A code-generation agent. All invocations are non-interactive except plan/init."""

    @abc.abstractmethod
    def build(self, ctx: BuildContext) -> BuildResponse: ...

    @abc.abstractmethod
    def validate(self, ctx: BuildContext, validation: Validation | ValidationFile) -> ValidationResponse: ...

    @abc.abstractmethod
    def difference(self, ctx: DifferencingContext) -> DifferencingResponse: ...

    @abc.abstractmethod
    def plan(self, ctx: BuildContext) -> None: ...

    @abc.abstractmethod
    def init(self, project_name: str, intent_dir: str, prompt: str | None = None) -> None: ...

    @abc.abstractmethod
    def get_name(self) -> str: ...

    @abc.abstractmethod
    def get_type(self) -> str: ...


def _single_validation(validation: Validation | ValidationFile) -> Validation | None:
    if isinstance(validation, ValidationFile):
        return validation.validations[0] if validation.validations else None
    return validation


def read_response_json(path: str) -> dict[str, Any]:
    """Read a response file. Missing/empty/invalid → AgentError with the path in the message."""
    if not path:
        raise AgentError("No response file path was provided to the agent")
    p = Path(path)
    if not p.exists():
        raise AgentError(f"Response file not found: {path}")
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        raise AgentError(f"Response file is empty: {path}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentError(f"Response file contains invalid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentError(f"Response file must contain a JSON object: {path}")
    return data


def _scan_output(output_dir: str) -> list[str]:
    root = Path(output_dir)
    if not root.is_dir():
        return []
    return sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and ".git" not in p.parts
    )


# ---------------------------------------------------------------------------
# CLIAgent
# ---------------------------------------------------------------------------


class CLIAgent(Agent):
    """Wraps any command-line tool: prompt goes to stdin, response comes back via file."""

    def __init__(self, profile: AgentProfile, log: LogFn | None = None) -> None:
        self._profile = profile
        self._log = log or _noop_log
        self._templates = profile.prompt_templates or load_default_prompts()

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "cli"

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt(self._templates.build, ctx)
        self._run(prompt, cwd=None, env={"INTENTC_RESPONSE_FILE": ctx.response_file_path})
        return BuildResponse(**read_response_json(ctx.response_file_path))

    def validate(self, ctx: BuildContext, validation: Validation | ValidationFile) -> ValidationResponse:
        prompt = render_prompt(self._templates.validate_template, ctx, _single_validation(validation))
        self._run(prompt, cwd=None, env={"INTENTC_RESPONSE_FILE": ctx.response_file_path})
        return ValidationResponse(**read_response_json(ctx.response_file_path))

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        prompt = render_differencing_prompt(self._templates.difference, ctx)
        self._run(prompt, cwd=None, env={"INTENTC_RESPONSE_FILE": ctx.response_file_path})
        return DifferencingResponse(**read_response_json(ctx.response_file_path))

    def plan(self, ctx: BuildContext) -> None:
        # Generic CLI tools have no REPL contract: single-shot invocation with the plan prompt.
        self._run(render_prompt(self._templates.plan, ctx), cwd=None, env={})

    def init(self, project_name: str, intent_dir: str, prompt: str | None = None) -> None:
        rendered = render_init_prompt(self._templates.init, project_name, prompt)
        self._run(rendered, cwd=str(Path(intent_dir).parent), env={})

    def _run(self, prompt: str, cwd: str | None, env: dict[str, str]) -> None:
        if not self._profile.command:
            raise AgentError(
                f"Agent profile '{self._profile.name}' uses provider 'cli' but has no 'command' configured"
            )
        cmd = self._profile.command.split() + list(self._profile.cli_args)
        self._log(f"    agent: running {' '.join(cmd)}")
        full_env = dict(os.environ)
        full_env.update(env)
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._profile.timeout,
                cwd=cwd,
                env=full_env,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentError(f"Agent command timed out after {self._profile.timeout:.0f}s: {cmd[0]}") from exc
        except OSError as exc:
            raise AgentError(f"Failed to run agent command '{cmd[0]}': {exc}") from exc
        for line in (proc.stdout or "").splitlines():
            if line.strip():
                self._log(f"    agent: {line}")
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-10:]
            raise AgentError(
                f"Agent command failed (exit {proc.returncode}): {cmd[0]}\n" + "\n".join(tail)
            )


# ---------------------------------------------------------------------------
# ClaudeAgent
# ---------------------------------------------------------------------------


class ClaudeAgent(Agent):
    """Claude Code specialisation: streaming JSON output, OS-level sandbox, REPL for plan/init."""

    def __init__(self, profile: AgentProfile, log: LogFn | None = None) -> None:
        self._profile = profile
        self._log = log or _noop_log
        self._templates = profile.prompt_templates or load_default_prompts()

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "claude"

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt(self._templates.build, ctx)
        self._run_non_interactive(prompt, cwd=os.getcwd())
        if not Path(ctx.response_file_path).exists():
            self._log("    agent: no response file written — synthesising one from the output directory")
            return BuildResponse(
                status="success",
                summary="Build completed (agent did not write a response file; manifest synthesised from output directory)",
                files_created=_scan_output(ctx.output_dir),
            )
        return BuildResponse(**read_response_json(ctx.response_file_path))

    def validate(self, ctx: BuildContext, validation: Validation | ValidationFile) -> ValidationResponse:
        prompt = render_prompt(self._templates.validate_template, ctx, _single_validation(validation))
        self._run_non_interactive(prompt, cwd=os.getcwd())
        return ValidationResponse(**read_response_json(ctx.response_file_path))

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        prompt = render_differencing_prompt(self._templates.difference, ctx)
        self._run_non_interactive(prompt, cwd=os.getcwd())
        return DifferencingResponse(**read_response_json(ctx.response_file_path))

    def plan(self, ctx: BuildContext) -> None:
        self._run_interactive(render_prompt(self._templates.plan, ctx), cwd=os.getcwd())

    def init(self, project_name: str, intent_dir: str, prompt: str | None = None) -> None:
        rendered = render_init_prompt(self._templates.init, project_name, prompt)
        root = str(Path(intent_dir).parent)
        if prompt is not None:
            self._run_non_interactive(rendered, cwd=root)
        else:
            self._run_interactive(rendered, cwd=root)

    # -- command construction ------------------------------------------------

    def build_command(self, prompt: str) -> list[str]:
        cmd = ["claude", "-p", prompt, "--verbose", "--output-format", "stream-json", "--dangerously-skip-permissions"]
        if self._profile.model_id:
            cmd += ["--model", self._profile.model_id]
        if self._profile.effort:
            cmd += ["--effort", self._profile.effort]
        cmd += list(self._profile.cli_args)
        return cmd

    def interactive_command(self, prompt: str) -> list[str]:
        cmd = ["claude"]
        if self._profile.model_id:
            cmd += ["--model", self._profile.model_id]
        if self._profile.effort:
            cmd += ["--effort", self._profile.effort]
        cmd += list(self._profile.cli_args)
        cmd.append(prompt)
        return cmd

    def sandbox_settings(self) -> dict[str, Any] | None:
        if not self._profile.sandbox_write_paths and not self._profile.sandbox_read_paths:
            return None
        return {
            "permissions": {"allow": ["Bash(*)", "WebFetch(*)", "WebSearch(*)"], "deny": []},
            "sandbox": {
                "enabled": True,
                "write_paths": list(self._profile.sandbox_write_paths),
                "read_paths": list(self._profile.sandbox_read_paths),
            },
        }

    def _write_sandbox_settings(self, cwd: str) -> str | None:
        settings = self.sandbox_settings()
        if settings is None:
            return None
        claude_dir = Path(cwd) / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        path = claude_dir / "settings.local.json"
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return str(path)

    # -- execution -----------------------------------------------------------

    def _run_non_interactive(self, prompt: str, cwd: str) -> None:
        settings_path = self._write_sandbox_settings(cwd)
        self._log(f"    agent: starting claude ({len(prompt)} char prompt)")
        try:
            try:
                proc = subprocess.Popen(
                    self.build_command(prompt),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    cwd=cwd,
                )
            except OSError as exc:
                raise AgentError(
                    f"Could not start 'claude': {exc}. Is Claude Code installed and on PATH?"
                ) from exc
            assert proc.stdout is not None
            for line in proc.stdout:
                self._handle_stream_line(line)
            try:
                code = proc.wait(timeout=self._profile.timeout)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                raise AgentError(f"claude timed out after {self._profile.timeout:.0f}s") from exc
            if code != 0:
                raise AgentError(f"claude exited with code {code}")
        finally:
            if settings_path and os.path.exists(settings_path):
                os.remove(settings_path)

    def _handle_stream_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if event.get("type") != "assistant":
            return
        for block in event.get("message", {}).get("content", []) or []:
            if block.get("type") == "text":
                for text_line in str(block.get("text", "")).splitlines():
                    if text_line.strip():
                        self._log(f"    agent: {text_line}")

    def _run_interactive(self, prompt: str, cwd: str) -> None:
        self._log("    agent: starting claude (interactive)")
        try:
            subprocess.run(self.interactive_command(prompt), cwd=cwd, check=False)
        except OSError as exc:
            raise AgentError(f"Could not start 'claude': {exc}. Is Claude Code installed and on PATH?") from exc


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------


class MockAgent(Agent):
    """Records every call and returns configurable responses. For tests."""

    def __init__(
        self,
        name: str = "mock",
        build_response: BuildResponse | None = None,
        validation_response: ValidationResponse | None = None,
        differencing_response: DifferencingResponse | None = None,
        build_side_effect: Callable[[BuildContext], BuildResponse] | None = None,
        validate_side_effect: Callable[[BuildContext, Validation | None], ValidationResponse] | None = None,
    ) -> None:
        self._name = name
        self.build_response = build_response or BuildResponse(status="success", summary="ok")
        self.validation_response = validation_response or ValidationResponse(name="mock", status="pass", reason="ok")
        self.differencing_response = differencing_response or DifferencingResponse(
            status="equivalent", summary="mock"
        )
        self.build_side_effect = build_side_effect
        self.validate_side_effect = validate_side_effect
        self.build_calls: list[BuildContext] = []
        self.validate_calls: list[tuple[BuildContext, Validation | None]] = []
        self.difference_calls: list[DifferencingContext] = []
        self.plan_calls: list[BuildContext] = []
        self.init_calls: list[tuple[str, str, str | None]] = []

    def get_name(self) -> str:
        return self._name

    def get_type(self) -> str:
        return "mock"

    def build(self, ctx: BuildContext) -> BuildResponse:
        self.build_calls.append(ctx)
        response = self.build_side_effect(ctx) if self.build_side_effect else self.build_response
        if ctx.response_file_path:
            Path(ctx.response_file_path).parent.mkdir(parents=True, exist_ok=True)
            Path(ctx.response_file_path).write_text(response.model_dump_json(), encoding="utf-8")
        return response

    def validate(self, ctx: BuildContext, validation: Validation | ValidationFile) -> ValidationResponse:
        single = _single_validation(validation)
        self.validate_calls.append((ctx, single))
        if self.validate_side_effect:
            response = self.validate_side_effect(ctx, single)
        else:
            response = self.validation_response.model_copy(update={"name": single.name if single else self.validation_response.name})
        if ctx.response_file_path:
            Path(ctx.response_file_path).parent.mkdir(parents=True, exist_ok=True)
            Path(ctx.response_file_path).write_text(response.model_dump_json(), encoding="utf-8")
        return response

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        self.difference_calls.append(ctx)
        if ctx.response_file_path:
            Path(ctx.response_file_path).write_text(self.differencing_response.model_dump_json(), encoding="utf-8")
        return self.differencing_response

    def plan(self, ctx: BuildContext) -> None:
        self.plan_calls.append(ctx)

    def init(self, project_name: str, intent_dir: str, prompt: str | None = None) -> None:
        self.init_calls.append((project_name, intent_dir, prompt))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

PROVIDERS = ("claude", "cli")


def create_from_profile(profile: AgentProfile, log: LogFn | None = None) -> Agent:
    """Create the agent for a profile's provider. Unknown providers raise AgentError."""
    provider = (profile.provider or "").lower()
    if provider == "claude":
        return ClaudeAgent(profile, log=log)
    if provider == "cli":
        return CLIAgent(profile, log=log)
    raise AgentError(
        f"Unknown agent provider '{profile.provider}' in profile '{profile.name}'. "
        f"Supported providers: {', '.join(PROVIDERS)}"
    )
