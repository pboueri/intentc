# intentc

[A Compiler of Intent](https://pboueri.github.io/blog/compilers-of-intent/)


An experimental project where you spec out what you want to build in a well defined DAG, iterate and validate until its correct. If a new model, or new programming language comes along, no problem. Rebuild it with a new target. 

```
  intent/                          src/ (generated)
  ───────                          ────────────────

  project.ic
  implementations/
    default.ic
        │
        ▼
  models/models.ic  ──────────────► models/         ✓ validated
        │
        ▼
   store/store.ic   ──────────────► store/           ✓ validated
        │
        ▼
    api/api.ic      ──────────────► api/             ✗ failed
    api/validations.icv                 └─ on disk, not committed
        │
        ▼
   cli/cli.ic       (blocked, waiting on api)
```


---

## Quick Start

**Requirements:** Python 3.11+, [uv](https://docs.astral.sh/uv/), Claude Code (`claude` in PATH)

```bash
# Install
uv tool install git+https://github.com/pboueri/intentc

# Create a new project
mkdir my-project && cd my-project
intentc init my-project

# Write your first feature in intent/my-feature/my-feature.ic (+ validations.icv)
# Lint the intent project (milliseconds, no agent), then build it
intentc check
intentc build

# Check what was generated
intentc status
intentc diff my-feature
intentc log my-feature
```

---

## How It Works

intentc projects have two directories: `intent/` (your specs) and an output directory (generated code, default `src/`).

**Intent files (`.ic`)** are markdown files with YAML frontmatter. Each file describes one feature and optionally lists dependencies on other features. Together they form a DAG.

**Validation files (`.icv`)** describe what the generated code must do — they're checked automatically after each build. Think of them as tests the agent must pass. Three kinds are built in:

| type | what it checks | cost |
|------|----------------|------|
| `command_validation` | a shell command exits 0 (e.g. `pytest {output_dir} -q`) | deterministic, milliseconds |
| `file_exists` | every listed path/glob exists in the output directory | deterministic, milliseconds |
| `agent_validation` | an agent judges a natural-language rubric | one agent round-trip |

Deterministic checks run first; if one fails, the expensive agent rubrics are skipped for that attempt. A failure of `severity: warning` is reported but never blocks a build.

When you run `intentc build`, it:
1. Lints the project (`intentc check`) and refuses to start on errors
2. Marks anything whose intent files changed since their last build as outdated (by content hash, so `git checkout` and `touch` don't fool it)
3. Topologically sorts pending/outdated/failed features
4. Calls the configured agent (Claude Code by default) for each one, retrying with the previous failure reasons in the prompt
5. Runs validations after each build
6. Commits the generated code to git on success and tells you what you can build next

Failed builds leave files on disk for inspection but don't get committed. `intentc log <feature>` shows the history, steps and validation results of any target.

## Commands

| command | purpose |
|---------|---------|
| `intentc init [name]` | start a project (agent-guided; `--no-interactive` for a skeleton, `-P "desc"` for one-shot) |
| `intentc check` | lint intents: parse errors, unknown deps, cycles, mismatched targets, missing validations |
| `intentc build [target]` | build pending/outdated features (`-f` force, `-n` dry run, `-i` implementation) |
| `intentc validate [target]` | run validations without building (`project` runs only the assertions) |
| `intentc status` | DAG-ordered table of every feature's state, plus what to build next |
| `intentc diff <target>` | what the last build of a target generated (`--stat` for file list) |
| `intentc log <target>` | build history, latest steps, and recorded validation results |
| `intentc clean <target>` | revert a target's generated files and mark its dependents outdated |
| `intentc plan <target> "<prompt>"` | refine a feature's intent with the agent interactively |
| `intentc compare <dir_a> <dir_b>` | agent-judged functional equivalence of two builds |

Exit codes: `0` success, `1` a build or validation failed, `2` usage error (bad target, broken intent, malformed config).

---

## Project Structure

```
my-project/
├── intent/
│   ├── project.ic              # What this project is
│   ├── implementations/        # Language, stack, conventions
│   │   ├── default.ic          # Default implementation
│   │   └── {alt}.ic            # Alternative implementations
│   └── {module}/
│       └── {feature}/
│           ├── feature.ic      # What this feature should do
│           └── validations.icv # How to verify it worked
├── src/                        # Generated code (committed to git)
└── .intentc/
    └── config.yaml             # Agent and output dir config
```

Multiple implementations let you build the same specs to different targets:
```bash
# Build with default implementation
intentc build

# Build with a specific implementation
intentc build --implementation rust -o src_rust/
```