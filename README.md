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

# Write your first feature in intent/features/my-feature/feature.ic
# then build it
intentc build

# Check what was generated
intentc status
intentc diff features/my-feature
```

---

## How It Works

intentc projects have two directories: `intent/` (your specs) and an output directory (generated code, default `src/`).

**Intent files (`.ic`)** are markdown files with YAML frontmatter. Each file describes one feature and optionally lists dependencies on other features. Together they form a DAG.

**Validation files (`.icv`)** describe what the generated code must do — they're checked automatically after each build. Think of them as tests the agent must pass.

When you run `intentc build`, it:
1. Topologically sorts pending features
2. Calls the configured agent (Claude Code by default) for each one
3. Runs validations after each build
4. Commits the generated code to git on success

Failed builds leave files on disk for inspection but don't get committed.

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