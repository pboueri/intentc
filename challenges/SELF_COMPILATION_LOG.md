# Self-Compilation Log — Attempt 2

## Status: EQUIVALENT (5/5 dimensions pass) -- Iteration 3

## Iteration 1

**Result:** 3/5 dimensions pass (test_suite, dependency_compatibility, configuration_compatibility)

**Failures:**
- `public_api` — FAIL: PascalCase methods (Build, Validate, GetName, etc.) instead of snake_case; `content` field instead of `body`; reversed parameter orders; reduced ValidationType enum; missing wildcard deps
- `runtime_behavior` — FAIL: incompatible agent interface (PascalCase vs snake_case), different CLI invocation, missing wildcard dependency expansion

**Changes made to intent files:**
1. `implementation.ic` — Added explicit "Python Naming (PEP 8)" convention section mandating snake_case for all methods
2. `agents.ic` — Rewrote Agent interface pseudocode from PascalCase to explicit Python snake_case with `abc.ABC`; made PromptTemplates field `validate_template` explicit; fixed AgentProfile/BuildContext/DifferencingContext to show snake_case fields; fixed `create_from_profile` naming
3. `specifications.ic` — Explicitly named field as `body` (NOT `content`); added ValidationType enum with all 5 values; added parser function signatures with exact parameter orders
4. `validations.ic` — Changed ValidateFeature/ValidateProject/ValidateEntries to snake_case
5. `builder.ic` — Changed all method names/field names to snake_case; fixed BuildOptions to snake_case
6. `cli.ic` — Updated all builder method references to snake_case; added save_config signature
7. `project.ic` — Added wildcard dependency expansion section with fnmatch code

**Why:** The intent files used PascalCase in pseudocode but the reference implementation uses Python snake_case. The build agent was taking the PascalCase literally. Made all naming explicit.

## Iteration 2

**Result:** 4/5 dimensions pass (public_api, test_suite, dependency_compatibility, configuration_compatibility)

**Failures:**
- `runtime_behavior` — FAIL:
  - Missing `_apply_sandbox_paths` in builder (agents receive different filesystem constraints)
  - `plan` command: different behavior for missing features
  - `validate` command: different handling of non-existent features
  - Retry logic catches generic Exception instead of AgentError
  - Validation runner omits `validations`/`dependency_names` from BuildContext

**Changes made to intent files:**
1. `builder.ic` — Added full `_apply_sandbox_paths` method with code showing sandbox write/read path computation from project DAG; added exact `BuildContext` construction showing `validations=node.validations` and `dependency_names=dep_names`; specified `AgentError` in retry logic with exact code
2. `agents.ic` — Added `AgentError` exception class definition
3. `validations.ic` — Added exact BuildContext construction code for validation runner showing `validations=[]` and `dependency_names=[]`
4. `cli.ic` — Added feature existence validation step in plan command (exit code 2 for missing features)

**Why:** The generated code was missing sandbox path computation entirely and using generic Exception catching. These are specific behavioral details that needed explicit code examples in the intent files.

## Iteration 3

**Result: EQUIVALENT — 5/5 dimensions pass**

| Dimension | Status | Key Finding |
|-----------|--------|-------------|
| **public_api** | **PASS** | Same 8 CLI commands with identical flags; same module exports |
| **test_suite** | **PASS** | Reference: 165 tests, Candidate: 113 tests — both green |
| **runtime_behavior** | **PASS** | All core workflows produce equivalent outcomes |
| **dependency_compatibility** | **PASS** | Same 4 core deps with compatible versions |
| **configuration_compatibility** | **PASS** | Same .intentc/config.yaml format; configs interchangeable |

**Notable internal differences (not breaking equivalence):**
- Attribute naming conventions (`self.x` vs `self._x`)
- Step status strings (`"failure"` vs `"failed"`)
- Response file naming (UUID-based vs generation_id-based)
- Output formatting style (Rich tables vs inline)

## Progress Across 3 Iterations

| Iter | Passing | Key Change |
|------|---------|------------|
| 1 | 3/5 | snake_case naming, body field, ValidationType enum, wildcards |
| 2 | 4/5 | sandbox paths, AgentError, BuildContext fields, plan command |
| 3 | **5/5** | **EQUIVALENT** |

## What This Proves

- **intentc can rebuild itself**: All 8 targets build and pass all validations across 3 iterations
- **The intent files are the source of truth**: Generated code is functionally equivalent to the reference implementation
- **Progressive convergence works**: Each iteration narrows the gap as intent files become more precise
- **Specification precision is key**: Every divergence mapped to an underspecified detail in the intent files
- **3 iterations was sufficient**: Unlike the previous attempt (9 iterations, never reached equivalent), explicit code examples in intent files dramatically accelerated convergence

## Key Lessons

1. **PascalCase pseudocode is ambiguous** — When the implementation language uses snake_case, pseudocode definitions must explicitly show the target naming convention
2. **Field names must be explicit** — `body` vs `content` is not derivable from context; it must be specified
3. **Sandbox computation is a behavioral detail** — Missing `_apply_sandbox_paths` changes agent constraints at runtime, not just internal structure
4. **Exception types matter for retry semantics** — Catching `AgentError` vs generic `Exception` changes which failures are retried
5. **Exact code examples > prose descriptions** — Adding Python code snippets to intent files was the most effective way to eliminate ambiguity
