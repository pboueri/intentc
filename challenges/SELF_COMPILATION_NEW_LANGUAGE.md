# Self-Compilation New Language Challenge

## Goal

Prove that intent files can be made language-neutral and generate equivalent software in
both Python and Go. Starting from `intent/`, use `intentc build` to generate a Python
implementation and a Go implementation, then confirm both are functionally equivalent
to each other and to the existing `src/`.

The intent files are currently Python-specific. Making them language-neutral is **the work
of this challenge** — we don't pre-modify them. Instead, we iteratively refine intent
files and validation files to remove Python-specificity, moving language-specific details
into implementation files (`intent/implementations/default.ic` for Python,
`intent/implementations/go.ic` for Go). After each round of modifications, we rebuild
and compare until both builds pass.

## Prerequisites

- `intentc` installed and on PATH (run `uv tool install .` from the repo root)
- The repo is the intentc repo itself (`intent/` and `src/` both present)
- `intent/implementations/go.ic` exists with Go-specific implementation details

## Running

Use the companion script, which handles all isolation automatically:

```bash
./challenges/run_self_compilation_new_language.sh
```

This creates **two** hermetic temporary directories — one for Python, one for Go — each
containing only `intent/` and a throwaway git repo. No `src/`, no `.git` history from the
real repo, no `.intentc/state/`.

### Options

| Flag | Description |
|------|-------------|
| `--keep` | Preserve the temp directories after the run for inspection |
| `--skip-compare` | Build only; skip the `intentc compare` steps |
| `--target <t>` | Build a single target instead of the full DAG |
| `--no-force` | Don't pass `--force` (respect existing build state) |
| `--python-only` | Only build and compare the Python side |
| `--go-only` | Only build and compare the Go side |

### Examples

```bash
# Full new-language challenge with both comparisons
./challenges/run_self_compilation_new_language.sh

# Build only, keep output for manual inspection
./challenges/run_self_compilation_new_language.sh --skip-compare --keep

# Only test the Go side
./challenges/run_self_compilation_new_language.sh --go-only --keep

# Rebuild a single target
./challenges/run_self_compilation_new_language.sh --target build/builder --keep
```

## Three-Way Comparison

The challenge performs two comparisons:

1. **Python temp build ↔ existing `src/`** (same-language equivalence)
   Confirms the language-neutral intent files still produce a valid Python implementation.
   This is the same check as the original self-compilation challenge.

2. **Go temp build ↔ Python temp build** (cross-language equivalence)
   Confirms that the Go implementation is functionally equivalent to the Python one.
   This is a behavioral equivalence check — same CLI commands, same flags, same workflows,
   same outputs — not code identity.

Both comparisons must return `equivalent` for the challenge to pass.

## The Iterative Process

Like the original self-compilation challenge, this is done in rounds:

1. **Run the script.** See what fails.
2. **Identify Python-specific language** in intent files (e.g., references to `pydantic`,
   `snake_case`, `pyproject.toml`, Python-specific patterns).
3. **Refactor intent files** to be language-neutral, moving language-specific details
   into the implementation files. For example:
   - "Use pydantic for data modeling" → "Use the data modeling approach specified in the implementation file"
   - "snake_case methods" → defer to implementation file's naming conventions
   - "pyproject.toml" → "the language's standard dependency manifest"
4. **Re-run the script.** Check if the changes improved things.
5. **Repeat** until both comparisons pass.

You may also need to:
- Update `src/` if you find bugs in the existing implementation
- Add details to `intent/implementations/go.ic` if the Go agent needs more guidance
- Adjust validation files that assume Python-specific behavior

## Done State

The challenge is complete when:

```bash
./challenges/run_self_compilation_new_language.sh
```

exits with code `0` and both comparison outputs contain:

```
status: equivalent
```

All dimensions must pass for both comparisons (`public_api`, `test_suite`,
`runtime_behavior`, `dependency_compatibility`, `configuration_compatibility`).
A `divergent` result on any dimension means the challenge is not yet complete.

## Log

Maintain a running log at `challenges/SELF_COMPILATION_NEW_LANGUAGE_LOG.md` throughout
the challenge. After each iteration, append an entry with:

- What you tried
- What the result was (which targets failed, which dimensions were divergent)
- What you changed (intent files, implementation files, src/ files, or re-ran a build)
- Why you made that choice

This log is part of the challenge — it documents what it takes to achieve cross-language
compilation and informs future language additions.

## Notes

- Use your best judgement when fixing divergences. You may:
  - Update `intent/` files to remove language-specific assumptions
  - Update `intent/implementations/default.ic` or `go.ic` to add language-specific guidance
  - Update `src/` directly if you identify bugs that don't match the spec
  - Re-run `intentc build` for specific targets after making changes
- The goal is behavioral equivalence at CLI and interface boundaries, not code identity.
  A Python `dict` and a Go `map[string]interface{}` are equivalent if they produce the
  same JSON output.
- Prefer fixing intent files over patching generated code — language-neutral specs
  produce more portable results.
- Cross-language comparison focuses on: same CLI commands/flags, same configuration
  surface, same workflows, compatible dependency declarations via respective package
  systems.

Note: Aggressively use sub-agents to preserve context as needed.
