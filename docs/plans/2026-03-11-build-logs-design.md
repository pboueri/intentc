# Build Logs Design

## Problem

When building targets, it's hard to see what's happening. The builder currently emits sparse INFO-level log lines with no structure, timing, or diff information. After a build completes, there's no way to review what happened during each phase.

## Solution

Add structured build logs with discrete, timed steps that capture what the builder did at each phase. Logs are embedded in BuildResult and viewable both in real-time during builds and historically via a new `intentc log` command.

## Decisions

- **Storage**: Embed steps directly in BuildResult JSON (same `.intentc/state/` files)
- **Step source**: Builder-defined lifecycle phases wrapping agent.build()
- **Diff capture**: Git diff after build completes
- **Viewing**: Real-time progress during build + `intentc log` CLI command
- **Feature structure**: Two targets — `build/logs` (types + builder integration) and `cli/log` (CLI command + display)

## Core Types

### BuildPhase

Enum defining the discrete lifecycle phases of a build:

- `resolve_deps` — Resolve dependency chain from DAG
- `read_plan` — Read .ic spec and visible validations
- `build` — Invoke agent.build()
- `post_build` — File detection and git diff capture
- `validate` — Run validations against generated output

### StepStatus

Enum for per-step outcome:

- `success`
- `failed`
- `skipped`

### BuildStep

Captures one discrete phase:

```
BuildStep:
    phase: BuildPhase
    status: StepStatus
    started_at: timestamp
    ended_at: timestamp
    duration_seconds: float
    summary: string           # Brief description of what happened
    error: string             # Error message if failed (empty on success)
    files_changed: int        # Number of files affected (0 if N/A)
    diff_stat: string         # e.g. "3 files changed, +45 -12"
    diff: string              # Full unified diff (only on post_build)
```

### BuildResult Extension

BuildResult gains two new fields:

```
BuildResult:
    # ... existing fields ...
    steps: list[BuildStep]          # Ordered list of build steps (default empty)
    total_duration_seconds: float   # Wall-clock time for entire build (default 0)
```

The `steps` field defaults to empty list, preserving forward compatibility with existing serialized BuildResults.

## Builder Integration (`build/logs`)

The builder wraps each existing pipeline phase in timing and logging:

1. **resolve_deps** — Wraps DAG resolution + dependency chain lookup
   - Summary: "Resolved N dependencies: [dep1, dep2]"

2. **read_plan** — Wraps reading the .ic spec and visible validations
   - Summary: "Read {name}.ic with N validations"

3. **build** — Wraps the agent.build() call
   - Summary: "Agent generated N files" or error description

4. **post_build** — File detection + git diff on output directory
   - Summary: "N files changed (+X -Y)"
   - Captures diff_stat and full diff

5. **validate** — Runs validation for the target after build
   - Summary: "N/M validations passed" or "Validation skipped (no .icv)"

Each step is timed with monotonic clock. Steps are collected into a list and attached to BuildResult before saving. If a step fails, its status is set to `failed` with the error message, and subsequent steps are either skipped or not attempted.

### Real-time Output

During build, the builder emits structured progress lines to stderr:

```
[target-name] phase... done (duration)
[target-name] phase... FAILED (duration)
```

Example:
```
[core/intent] resolve_deps... done (0.1s)
[core/intent] read_plan... done (0.0s)
[core/intent] build... done (45.2s)
[core/intent] post_build... 3 files changed (+45 -12) (0.3s)
[core/intent] validate... 5/5 passed (1.2s)
Built core/intent (gen-1773256586) in 46.8s
```

## CLI Log Command (`cli/log`)

```
intentc log [target] [flags]
```

Flags:
- `--generation <id>` — View a specific generation (default: latest)
- `--diff` — Show full unified diff output
- `-o, --output <dir>` — Output directory scope

### Summary View (default)

```
Build Log: core/intent (gen-1773256586)
Status: success | 2026-03-11T14:18:30

  [success]  resolve_deps   Resolved 3 dependencies           0.1s
  [success]  read_plan      Read core/intent.ic, 5 validations 0.0s
  [success]  build          Agent generated 2 files           45.2s
  [success]  post_build     3 files changed (+45 -12)          0.3s
  [success]  validate       5/5 validations passed             1.2s

Total: 46.8s | Files: 3 changed
```

### Diff View (`--diff`)

Appends the full unified diff after the summary table.

### No Target Specified

Lists all targets with their latest build summary (one line per target):

```
Build History:
  core/intent      gen-1773256586  success  46.8s  3 files  2026-03-11T14:18
  core/schema      gen-1773256590  success  12.3s  1 file   2026-03-11T14:19
  agents/claude    gen-1773256600  failed    8.1s  0 files  2026-03-11T14:20
```

## Feature Structure

### `intent/build/logs/` — build/logs target
- `logs.ic` — Types (BuildPhase, StepStatus, BuildStep) + builder integration
- `validations.icv` — Validates types exist, builder emits steps, diffs are captured
- Depends on: `build/builder`, `build/state`, `build/git`

### `intent/cli/log/` — cli/log target
- `log.ic` — The `intentc log` command + real-time build output formatting
- `validations.icv` — Validates CLI command exists and produces correct output
- Depends on: `build/logs`, `cli/commands`
