# intentc

A compiler of intent. intentc transforms loosely-specified intents into working code using AI coding agents.

Developers author spec files describing *what* they want. intentc orchestrates AI agents to generate *how*.

## How It Works

1. **Write intent files** (`.ic`) describing features in plain language with YAML metadata
2. **Write validation files** (`.icv`) defining constraints on generated code
3. **Run `intentc build`** to have an AI agent generate code from your specs
4. **Run `intentc validate`** to verify the output meets your constraints

Intent files form a dependency graph (DAG). intentc resolves build order automatically and builds targets in dependency order.

## Installation

```sh
# Install with uv
uv tool install intentc

# Or add as a dev dependency
uv add --dev intentc
```

Requires Python 3.11+, git, and a configured AI agent (e.g., Claude Code).

## Project Structure

```
project-root/
  .intentc/
    config.yaml              # Agent profiles, build config, logging
  intent/
    project.ic               # Project-level intent (global context for all builds)
    feature-a/
      feature-a.ic           # Feature intent spec
      validations.icv         # Validation constraints
    feature-b/
      feature-b.ic
      validations.icv
  build-default/              # Generated code output (configurable)
  pyproject.toml              # Package config (managed by uv)
```

## Quick Start

```sh
# Initialize a new intentc project (must be in a git repo)
intentc init

# Write your project intent and feature specs
# ...edit intent/project.ic and intent/<feature>/<feature>.ic...

# Validate spec files parse correctly
intentc check

# Build all targets
intentc build --output build-default

# Build a specific target
intentc build auth --output build-default

# Validate generated code against constraints
intentc validate --output build-default

# View build status and dependency tree
intentc status --tree
```

## CLI Reference

Built with [typer](https://typer.tiangolo.com/). Data models powered by [pydantic](https://docs.pydantic.dev/).

### Global Flags

```
-v, --verbose              Increase verbosity (-v = info, -vv = debug)
    --config <file>        Override config file path
    --profile <name>       Agent profile to use (default: "default")
    --agent-provider <p>   Override agent provider
    --agent-command <cmd>  Override agent command
    --agent-timeout <dur>  Override agent timeout
    --agent-retries <n>    Override agent retries
    --agent-cli-args <a>   Override agent CLI args
    --model <id>           Override model identifier
    --log-level <level>    Override log level
```

### Commands

| Command | Description |
|---------|-------------|
| `intentc init` | Initialize project structure (`.intentc/`, `intent/`, template `project.ic`) |
| `intentc build [target]` | Build targets from specs using AI agent |
| `intentc clean [target]` | Remove generated files |
| `intentc validate [target]` | Run validations against generated code |
| `intentc status` | Show target status and dependency tree |
| `intentc commit` | Commit changes with `intent:` / `generated:` prefixes |
| `intentc check [target]` | Validate spec files against schemas |
| `intentc add intent <name>` | Scaffold a new feature intent |
| `intentc add validation <target> <type>` | Add a validation to an existing target |
| `intentc list intents` | List all discovered features |
| `intentc list validations` | List available validation types |
| `intentc list profiles` | List configured agent profiles |

### `intentc build`

```
intentc build [target] [flags]

Flags:
  -f, --force              Force rebuild even if up to date
      --dry-run            Show what would be built without building
  -o, --output <dir>       Output directory for generated code
  -p, --profile <name>     Agent profile to use
```

Build pipeline:
1. Schema-validate all spec files and config
2. Discover and parse all intent/validation files
3. Build dependency DAG, detect cycles
4. Determine build set (target + transitive deps, or all unbuilt)
5. Resolve output directory
6. For each target in topological order: resolve agent profile, invoke agent, track result

### `intentc validate`

```
intentc validate [target] [flags]

Flags:
  -o, --output <dir>       Output directory to validate against
  -p, --profile <name>     Agent profile for LLM judge validations
      --parallel           Run validations concurrently
      --timeout <dur>      Per-validation timeout
```

Validation types:
- **file_check** — Verify a file exists and contains expected strings
- **folder_check** — Verify a directory exists with expected children
- **command_check** — Run a shell command, check exit code and output
- **llm_judge** — AI evaluates generated code against a natural language rubric

### `intentc commit`

Separates changes into intent files (`intent/` directory) and generated files, creating separate commits with appropriate prefixes:
- `intent: <message>` for spec changes
- `generated: <message>` for generated code changes

## Agent Profiles

Agents are configured through named profiles in `.intentc/config.yaml`:

```yaml
version: 1
profiles:
  default:
    provider: claude          # "claude", "codex", or "cli"
    timeout: 5m
    retries: 3
    model_id: claude-sonnet-4-6
    tools:
      - name: bash
        enabled: true
      - name: file_read
        enabled: true
      - name: file_write
        enabled: true
  fast:
    provider: claude
    model_id: claude-haiku-4-5
    timeout: 2m
    retries: 1
build:
  default_output: build-default
logging:
  level: info
```

Targets can specify which profile to use via the `profile:` field in their `.ic` frontmatter. The CLI `--profile` flag overrides all per-target selections.

## Spec File Formats

### Intent Files (.ic)

```yaml
---
name: auth                     # Must match directory name
version: 1
depends_on: [core, database]   # Dependencies (forms a DAG)
tags: [security]
profile: default               # Optional: agent profile override
---

# Authentication System

Describe what this feature should do in plain language...
```

### Validation Files (.icv)

```yaml
---
target: auth
version: 1
judge_profile: review          # Optional: separate agent for LLM judge
validations:
  - name: auth-module-exists
    type: folder_check
    path: src/auth

  - name: auth-tests-pass
    type: command_check
    command: uv run pytest src/auth/ -v
    exit_code: 0

  - name: auth-quality
    type: llm_judge
    rubric: |
      Evaluate the auth module for security best practices...
    severity: error
    context_files: ["src/auth/**"]
---
```

## Development

```sh
# Install dependencies
uv sync

# Run tests
uv run pytest -v

# Run intentc locally
uv run intentc --help
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Validation failures, build failures, or user errors |
| 2 | Internal errors |
