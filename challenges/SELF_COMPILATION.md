# Self-Compilation Challenge

## Goal

Prove that `intentc` can rebuild itself. Starting from `intent/`, use `intentc build` to
regenerate the entire `src/` into a fresh output directory, then confirm it is functionally
equivalent to the original using `intentc compare`.

The build must be hermetic: the agent must never see the existing implementation, git
history, or any other artifact that would let it copy rather than generate. If the
challenge passes, the intent files *are* the source — the generated code is a
reproducible artifact, not the source of truth.

## Prerequisites

- `intentc` installed and on PATH (run `uv tool install .` from the repo root)
- The repo is the intentc repo itself (`intent/` and `src/` both present)

## Running

Use the companion script, which handles all isolation automatically:

```bash
./challenges/run_self_compilation.sh
```

This creates a temporary directory at `/tmp/intentc-selfcompile-<timestamp>/` containing
**only** `intent/` and a throwaway git repo. No `src/`, no `.git` history from the real
repo, no `.intentc/state/` — the agent has nothing to cheat from. The build runs entirely
inside this temp dir, and the comparison runs against the real `src/` afterward.

### Options

| Flag | Description |
|------|-------------|
| `--keep` | Preserve the temp directory after the run for inspection |
| `--skip-compare` | Build only; skip the `intentc compare` step |
| `--target <t>` | Build a single target instead of the full DAG |
| `--no-force` | Don't pass `--force` (respect existing build state) |

### Examples

```bash
# Full self-compilation with comparison
./challenges/run_self_compilation.sh

# Build only, keep the output for manual inspection
./challenges/run_self_compilation.sh --skip-compare --keep

# Rebuild a single target
./challenges/run_self_compilation.sh --target build/builder --keep
```

## Why Isolation Matters

The build agent is an LLM with tool access. Without isolation it can:

- Read `src/` directly and copy the existing implementation
- Run `git log`, `git show`, or `git diff` to reconstruct the source from history
- Read `.intentc/state/` to find prior build responses containing generated code

The script prevents all of these by running the build in a temp directory that contains
only intent files and an empty git repo (required by the build system's checkpoint
mechanism, but containing no useful history). This is a stronger guarantee than
sandboxing rules alone, because the information simply does not exist on disk.

## Manual Steps (if needed)

If you need to run steps individually instead of using the script:

### 1. Build a specific target after a failure

```bash
# Re-run from the preserved work dir
cd /tmp/intentc-selfcompile-<timestamp>/
intentc build <target> --output-dir src_generated/ --force
```

### 2. Compare manually

```bash
intentc compare /path/to/repo/src/ /tmp/intentc-selfcompile-<timestamp>/src_generated/
```

### 3. Fix and repeat

If the result is `divergent`, the report will show which dimensions failed and why.
Fix the relevant intent files in the real repo, then re-run the script:

```bash
./challenges/run_self_compilation.sh
```

## Done State

The challenge is complete when:

```bash
./challenges/run_self_compilation.sh
```

exits with code `0` and the comparison output contains:

```
status: equivalent
```

All dimensions must pass (`public_api`, `test_suite`, `runtime_behavior`,
`dependency_compatibility`, `configuration_compatibility`). A `divergent` result on any
dimension means the challenge is not yet complete.

## Log

Maintain a running log at `challenges/SELF_COMPILATION_LOG.md` throughout the challenge.
After each iteration, append an entry with:

- What you tried
- What the result was (which targets failed, which dimensions were divergent)
- What you changed (intent files, src/ files, or re-ran a build)
- Why you made that choice

This log is part of the challenge — it documents what it takes to achieve self-compilation
and informs future improvements to the intent files.

## Notes

- Use your best judgement when fixing divergences. You may:
  - Update `intent/` files to improve the spec to be more detailed and less ambiguous so the agent produces better output
  - Update `src/` directly if you identify bugs in the existing implementation that do not match the spec
  - Re-run `intentc build` for specific targets after making changes
- The goal is equivalence, not byte-for-byte identity. Prefer fixing intent files over
  patching generated code where possible, as better specs produce more reproducible results.
- `intentc compare` requires the `compare` CLI command to be implemented
  (see `intent/interfaces/cli/cli.ic`).

Note: Aggressively use sub-agents to preserve context as needed.
