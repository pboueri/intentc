# Self-Compilation Challenge: Less Specificity

## Goal

Prove that intent files can express desired behavior at a higher level of abstraction —
without pseudocode, without algorithm implementations, without exact code patterns — and
still produce functionally equivalent software. The intent files should read like
specifications of **what** the system does and **why**, not **how** it does it internally.

## Problem Statement

The current intent files evolved through self-compilation iterations that drove them toward
increasing specificity. Pseudocode was added to resolve ambiguity. Exact field orderings,
algorithm implementations, retry loops, and CLI command construction were spelled out in
detail. This worked for achieving equivalence, but the intent files now read more like
poorly-written code than high-level specifications.

Intent files should express:
- **Desired behavior** — what the system does from the user's perspective
- **Interfaces and contracts** — the public API surface, CLI commands, type names
- **Constraints** — what must be true (e.g., "the agent must be sandboxed")
- **Relationships** — how components depend on and interact with each other

Intent files should NOT express:
- **Algorithms** — step-by-step pseudocode for internal methods
- **Implementation patterns** — exact code blocks, loop structures, error handling flow
- **Internal method signatures** — private helpers, exact parameter ordering of internals
- **Wiring details** — how to construct objects field-by-field
- **Format strings** — exact log message templates, summary formats

## What to Change

### Must Remove

1. **Pseudocode blocks that implement algorithms.** The `_apply_sandbox_paths` method in
   `builder.ic` is ~30 lines of pseudocode computing filesystem boundaries. Replace with a
   behavioral description: "The builder scopes agent filesystem access based on the project
   DAG — read access to the target's ancestors' intent files, write access to the output
   directory and response staging areas."

2. **Retry loop implementations.** The retry loop in `builder.ic` is code. Replace with:
   "Retries apply only to AgentError (not validation failures). `retries=3` means 3 total
   attempts."

3. **Object construction patterns.** The exact `BuildContext(intent=..., validations=...,
   ...)` field-by-field construction appears in both `builder.ic` and `validations.ic`.
   Replace with: "Construct a BuildContext with the target's intent, validations, output
   directory, generation ID, dependency names, project intent, implementation, and response
   file path."

4. **CLI command construction.** The exact `["claude", "-p", prompt, "--verbose", ...]`
   arrays in `agents.ic`. Replace with behavioral description of what flags are needed and
   why.

5. **Wildcard expansion pseudocode.** The loop in `project.ic` implementing glob expansion.
   Replace with: "Wildcard patterns in depends_on are expanded via glob-style matching
   during load. Unmatched wildcards produce parse errors."

6. **SQL schema details.** The 9-table schema in `storage.ic` with exact column definitions.
   Replace with a description of what data is stored and the design principles (append-only
   audit trail, mutable target_state, etc.). The exact schema is an implementation detail.

7. **Exact log message formats.** The progress logging sections with exact format strings
   like `"[{index}/{total}] Building target '{target}'..."`. Replace with: "The builder
   emits progress callbacks at each significant step (build start, target start/skip/complete,
   validation, checkpoint, failure)."

8. **Exact summary format strings.** Like `"{passed}/{total} passed, {errors} error(s),
   {warnings} warning(s)"` in validations.ic.

9. **Internal helper function pseudocode.** Like `prompts_dir()`, `diff_prompts_dir()`,
   `_build_summary()`, `_load_project_or_exit()`. These can be described behaviorally.

### Must Keep

1. **Type names and field names** — these ARE the interface. `BuildContext.intent`,
   `TargetStatus.BUILT`, `AgentProfile.sandbox_write_paths` must be specified because
   other components depend on them.

2. **Public interfaces** — the Agent interface methods, StorageBackend abstract methods,
   ValidationRunner interface. These are contracts.

3. **CLI commands and flags** — `intentc build --force --output-dir` etc. This is the
   user-facing surface.

4. **Behavioral contracts** — "builds are atomic", "failed targets stop the DAG walk",
   "response files are deleted after storage". These express WHAT must be true.

5. **Enum values** — `TargetStatus` values, `ValidationType` values. These are part of
   the data contract.

6. **Exit codes** — these are part of the CLI contract.

7. **Design principles** — append-only audit trail, backend-agnostic storage, etc.

### Implementation Files (`default.ic`, `go.ic`)

The implementation files are also over-specified:

1. **Exact file path mappings.** The Module Layout sections in `default.ic` and `go.ic`
   list every single file path (`intentc/build/agents.py`, `internal/build/agents/agents.go`).
   These are implementation details the agent can derive from conventions. Replace with
   high-level guidance: "organize by domain, one module per intent feature" and let the
   agent apply the language's standard project layout conventions.

2. **Exact import patterns.** `default.ic` has a full Python-Specific Import Patterns
   section with exact `from intentc.build.agents import ...` statements. Remove — the
   agent can derive imports from the module structure.

3. **Exact re-export lists.** Specifying exactly which symbols to re-export from `__init__.py`
   is over-constraining. The intent files already specify the public API — the implementation
   file doesn't need to duplicate it.

4. **Data modeling specifics.** "All data types use Pydantic `BaseModel` with
   `model_config = {"extra": "ignore"}`" is too prescriptive. Say "use a data modeling
   library for validated data types" and let the agent choose the idiomatic approach.

Implementation files should specify:
- Language and version
- Core library/framework choices (CLI framework, data modeling, storage)
- Naming conventions
- Project structure conventions (at a high level)
- Dependency injection philosophy

Implementation files should NOT specify:
- Every file path in the project
- Import statements
- Exact re-export lists (these belong in the intent files as interface contracts)
- Framework-specific configuration details

### Judgment Calls

Some specifications sit at the boundary. Use your judgment:

- **Type definitions with fields** — Keep the fields (they're interface), remove the
  defaults unless they're part of the contract. `timeout: float = 3600.0` is a contract;
  `summary: string = ""` is an implementation default.
- **Method signatures on abstract interfaces** — Keep signatures for public/abstract
  methods. Remove signatures for internal/private methods.
- **Build pipeline steps** — Keep the high-level sequence (determine build set → build
  each target → checkpoint). Remove the sub-step pseudocode within each.
- **Wiring patterns in CLI** — Keep the dependency injection pattern at a high level
  ("each command constructs its own dependencies"). Remove the exact constructor call
  sequence.

## Prerequisites

- `intentc` installed and on PATH
- The repo is the intentc repo itself (`intent/` and `src/` both present)
- The existing self-compilation challenge passes (baseline equivalence)

## Running

Use the companion script:

```bash
./challenges/run_self_compilation_less_specificity.sh
```

This creates a hermetic temporary directory containing only `intent/` and a throwaway
git repo. No `src/`, no `.git` history, no `.intentc/state/`.

### Options

| Flag | Description |
|------|-------------|
| `--keep` | Preserve the temp directory after the run |
| `--skip-compare` | Build only; skip `intentc compare` |
| `--target <t>` | Build a single target instead of the full DAG |
| `--no-force` | Don't pass `--force` |

### Examples

```bash
# Full challenge run
./challenges/run_self_compilation_less_specificity.sh

# Build only, keep output
./challenges/run_self_compilation_less_specificity.sh --skip-compare --keep

# Rebuild a single target after editing its intent
./challenges/run_self_compilation_less_specificity.sh --target build/builder --keep
```

## The Iterative Process

1. **Pick an intent file** with pseudocode or over-specification.
2. **Rewrite it** to be more abstract — replace pseudocode with behavioral bullet points,
   remove algorithm implementations, keep interfaces and contracts.
3. **Rebuild** from the modified intent files.
4. **Compare** the new build to existing `src/`.
5. **If divergent**, decide: is the divergence because the intent was too vague (add back
   a behavioral constraint), or because the existing code had an assumption that shouldn't
   be in the spec (update `src/`)?
6. **Repeat** until the intent files are satisfactorily abstract AND builds are equivalent.

### The Tension

Previous self-compilation challenges showed that MORE specificity → better convergence.
This challenge deliberately goes the other direction. The hypothesis is that there exists
a sweet spot: intent files that are abstract enough to be readable specifications (not code)
but precise enough about **behavior and contracts** that agents produce equivalent output.

When a less-specific intent produces a divergent build, the question is: should we add
specificity back, or should we accept that the divergence is acceptable (same behavior,
different internal approach)? The answer depends on whether the divergence is at the
**interface boundary** (must fix) or **internal implementation** (acceptable).

## Done State

The challenge is complete when:

1. The intent files contain **no pseudocode blocks** that implement algorithms or internal
   logic.
2. The intent files read as **behavioral specifications**, not code.
3. Running:
   ```bash
   ./challenges/run_self_compilation_less_specificity.sh
   ```
   exits with code `0` and the comparison output contains:
   ```
   status: equivalent
   ```
   All 5 dimensions must pass (`public_api`, `test_suite`, `runtime_behavior`,
   `dependency_compatibility`, `configuration_compatibility`).

## Log

Maintain a running log at `challenges/SELF_COMPILATION_LESS_SPECIFICITY_LOG.md`. After
each iteration, record:

- Which file(s) you made less specific
- What pseudocode/specificity you removed
- What behavioral description replaced it
- Whether the build was equivalent or divergent
- If divergent, what you learned and what you adjusted

This log documents what level of abstraction is achievable while maintaining equivalence.

## Suggested Order

Start with the files that have the most egregious pseudocode:

1. **`build/builder/builder.ic`** — `_apply_sandbox_paths`, retry loop, BuildContext
   construction, exact build pipeline sub-steps
2. **`build/agents/agents.ic`** — CLI command construction, prompt path resolution helpers
3. **`build/storage/storage.ic`** — Full SQL schema, exact method signatures
4. **`implementations/default.ic`** — Exact file paths, import patterns, re-export lists
5. **`implementations/go.ic`** — Same issues as default.ic for Go
6. **`core/project/project.ic`** — Wildcard expansion pseudocode
7. **`build/validations/validations.ic`** — BuildContext construction, summary format
8. **`interfaces/cli/cli.ic`** — Wiring pseudocode, render function signatures
9. **`core/specifications/specifications.ic`** — Parser function signatures
10. **`build/state/state.ic`** — Serialization details

## Notes

- Use your best judgment on the boundary between "interface contract" and "implementation
  detail". When in doubt, remove it — you can always add behavioral constraints back if
  the build diverges.
- The goal is NOT to make intent files shorter for the sake of brevity. The goal is to
  make them express intent at the right level of abstraction.
- If removing pseudocode causes divergence, try replacing it with a behavioral constraint
  first (e.g., "the agent must only have read access to ancestor intent files") before
  adding code back.
- Prefer fixing intent files over patching generated code.
- You may update `src/` if you discover the existing implementation has behavior that
  shouldn't be in the spec.

Note: Aggressively use sub-agents to preserve context as needed.
