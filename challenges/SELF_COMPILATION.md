# Self-Compilation Challenge

## Goal

Prove that `intentc` can rebuild itself. Starting from `intent/`, use `intentc build` to
regenerate the entire `src/` into a fresh output directory, then confirm it is functionally
equivalent to the original using `intentc compare`.

If the challenge passes, the intent files *are* the source — the generated code is
a reproducible artifact, not the source of truth.

## Prerequisites

- `intentc` installed and on PATH (run `uv tool install .` from the repo root)
- A clean git working tree (intentc commits generated code after each successful build)
- The repo is the intentc repo itself (`intent/` and `src/` both present)

## Steps

### 1. Build into a fresh output directory

```bash
intentc build --output-dir src_generated/ --force
```

This runs every target in `intent/` through the agent from scratch, committing each
successful build. Validations run automatically after each target. If a target fails,
inspect the build output, then re-run that target:

```bash
intentc build <target> --output-dir src_generated/ --force
```

Repeat until all targets build successfully.

### 2. Compare to the original

```bash
intentc compare src/ src_generated/
```

This runs the differencing evaluation across all standard dimensions: `public_api`,
`test_suite`, `runtime_behavior`, `dependency_compatibility`, and
`configuration_compatibility`.

### 3. Fix and repeat

If the result is `divergent`, the report will show which dimensions failed and why.
Re-run the relevant targets:

```bash
intentc build <target> --output-dir src_generated/ --force
```

Then re-run the comparison. Repeat until the result is `equivalent`.

## Done State

The challenge is complete when:

```bash
intentc compare src/ src_generated/
```

exits with code `0` and the report contains:

```
status: equivalent
```

All dimensions must pass. A `divergent` result on any dimension means the challenge
is not yet complete.

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
  - Update `intent/` files to improve the spec so the agent produces better output
  - Update `src/` directly if you identify bugs in the existing implementation
  - Re-run `intentc build` for specific targets after making changes
- The goal is equivalence, not byte-for-byte identity. Prefer fixing intent files over
  patching generated code where possible, as better specs produce more reproducible results.
- `intentc compare` requires the `compare` CLI command to be implemented
  (see `intent/interfaces/cli/cli.ic`).
