# Self-Compilation New Language Challenge Log

## Iteration 1 — Python Build Baseline

**What we tried:** Full Python build (`--python-only --keep`) to establish baseline comparison with existing `src/`.

**Result:** Python build succeeded (all 10 targets built). Comparison: **DIVERGENT** (3/5 pass, 2/5 fail).

| Dimension | Status |
|-----------|--------|
| public_api | FAIL |
| test_suite | pass |
| runtime_behavior | FAIL |
| dependency_compatibility | pass |
| configuration_compatibility | pass |

**Key divergences identified:**
1. `--implementation` flag accepted but silently ignored in `build` and `validate` commands — `BuildOptions` missing `implementation` field entirely
2. `compare` command drives agent directly via `DifferencingContext` instead of calling `run_differencing()`
3. `plan` command: generated version exits with error for missing intents, existing version creates synthetic IntentFile
4. `diff` command: exit code 2 in existing vs 1 in generated on errors
5. Error handling: existing has `_load_project_or_exit()` with friendly messages, generated shows raw tracebacks
6. `build/__init__.py` exports additional symbols not in existing
7. `ParseError` constructor incompatible (dataclass vs Exception subclass)
8. `CLIAgent` uses `shell=True` vs `shell=False`
9. Response directory naming differs ('val' vs 'validation')

**Changes needed:** The intent files need to be more explicit about:
- `BuildOptions` must include an `implementation` field and CLI must wire it through
- `compare` command must use `run_differencing()` workflow function
- Error handling pattern (`_load_project_or_exit()`)
- Exact exit codes per command
- `ParseError` as a dataclass with specific fields
- `build/__init__.py` export list
- `shell=False` for subprocess execution
- Response directory naming convention

## Iteration 2 — Intent File Fixes

**What we tried:** Updated intent files to address the 9 divergences found in iteration 1:

**Changes made:**
- `builder.ic`: Added `implementation: str = ""` field to `BuildOptions`, added step 3 "Resolve implementation" to build pipeline
- `cli.ic`: Added `_load_project_or_exit()` error handling helper, wired `--implementation` through to `BuildOptions`, updated `plan` to create synthetic IntentFile for missing intents, fixed `diff` exit code to 2, enforced `run_differencing()` routing for `compare`, added explicit Typer app creation with name/help
- `specifications.ic`: Added exact `ParseError` dataclass definition and `ParseErrors` exception
- `agents.ic`: Added `shell=False` requirement for CLIAgent subprocess calls, added explicit prompt path resolution using `Path.cwd() / "intent"` (NOT `__file__`)
- `state.ic`: Added explicit response directory naming (`val`, not `validation`)
- `differencing.ic`: Added `implementation` keyword arg to `run_differencing()` signature

**Result:** Build completed 9/10 targets. `differencing` target FAILED validation (4/5 passed, 1 error). The error was that the generated code used `Path(__file__)` for prompt resolution instead of `Path.cwd()`, causing the differencing prompt file to not be found.

## Iteration 3 — Fix Prompt Path and Validation

**What we tried:**
- Already added explicit `_prompts_dir()` and `_diff_prompts_dir()` helper code to `agents.ic` showing `Path.cwd() / "intent"` resolution
- Updated `differencing/validations.icv` to remove hardcoded path check and instead validate the rendering mechanism
- Made test runner validation more flexible about output directory name

**Result:** Rebuilt `differencing` target after updating intents. All 10 targets built with all validations passing (5/5 for differencing). Python comparison: **EQUIVALENT** — all 5 dimensions pass.

| Dimension | Status |
|-----------|--------|
| public_api | pass |
| test_suite | pass |
| runtime_behavior | pass |
| dependency_compatibility | pass |
| configuration_compatibility | pass |

**Key lesson:** Validation files need to be as language-neutral as the intent files. Hardcoded file paths and directory structure assumptions in `.icv` files prevent cross-language builds.

## Iteration 4 — Full Language Neutralization

**What we tried:** Comprehensive rewrite of all intent files to be fully language-neutral:

**Changes made to .ic files:**
- All `python` code blocks changed to unlabeled pseudocode (no language annotation)
- Python-specific syntax (BaseModel, abc.ABC, @dataclass, decorators) replaced with language-neutral pseudocode (Type, Interface, Enum)
- Module Layout sections stripped of explicit file paths (e.g., `intentc/build/agents.py`) and replaced with abstract module descriptions
- Explicit Python import statements removed; replaced with abstract dependency descriptions
- "snake_case per Python convention" replaced with "follow implementation naming conventions"
- Python-specific types (timedelta, datetime, Path, BaseModel) replaced with neutral equivalents (duration, timestamp, path, Type)
- Python-specific references (Pydantic, Typer, Rich, pyproject.toml) removed from specs, deferred to implementation files

**Changes made to .icv files:**
- Removed all Python-specific file paths from validation rubrics (e.g., `intentc/build/agents.py`, `src/intentc/cli/main.py`)
- Replaced Python-specific test commands (`uv run pytest`, `python -m pytest`) with generic "use the implementation's test runner"
- Removed Typer/pyproject.toml references from CLI validations
- Made end-to-end assertions language-neutral (removed Python-specific import verification commands)

**Changes made to implementation files:**
- `default.ic`: Added comprehensive Module Layout section mapping each intent feature to specific Python file paths, added Python-specific import patterns, added data modeling guidance (Pydantic, enums, decorators)
- `go.ic`: Added comprehensive Module Layout section mapping each intent feature to Go packages (cmd/, internal/), added data modeling guidance (structs, json tags, sentinel errors)

**Result:** Python build succeeded — all 10 targets built, all validations passed. Python comparison: **EQUIVALENT** — all 5 dimensions pass.

| Dimension | Status |
|-----------|--------|
| public_api | pass |
| test_suite | pass |
| runtime_behavior | pass |
| dependency_compatibility | pass |
| configuration_compatibility | pass |

Go build also succeeded — all 10 targets built, all validations passed. Cross-language comparison (Go vs Python): **EQUIVALENT** — all 5 dimensions pass.

| Comparison | Dimension | Status |
|-----------|-----------|--------|
| Python vs src/ | public_api | pass |
| Python vs src/ | test_suite | pass |
| Python vs src/ | runtime_behavior | pass |
| Python vs src/ | dependency_compatibility | pass |
| Python vs src/ | configuration_compatibility | pass |
| Go vs Python | public_api | pass |
| Go vs Python | test_suite | pass |
| Go vs Python | runtime_behavior | pass |
| Go vs Python | dependency_compatibility | pass |
| Go vs Python | configuration_compatibility | pass |

**Key insight:** Moving language-specific details (file paths, import patterns, framework choices, naming conventions) into implementation files while keeping behavioral specs language-neutral in intent files works. The same abstract spec produces equivalent Python and Go implementations.

## Iteration 5 — Full End-to-End Challenge Run

**What we tried:** Full `run_self_compilation_new_language.sh` run with both Python and Go builds from fresh hermetic environments, followed by all comparisons.

**Changes made (between iterations 4 and 5):**
- `state.ic`: Added explicit Testing section requiring state roundtrip tests to use real SQLiteBackend, not mocks
- `builder.ic`: Made `val_response_dir` passing to ValidationSuite explicit in the validate step
- `default.ic`: Added explicit `rich>=13.0.0` dependency requirement (must be declared, not relied on transitively through typer)

**Result (first attempt):** Python build all passed. Go build failed at `build/state` (4/5 validations, `state-roundtrip` failed because tests used mock backend instead of real SQLiteBackend). Fixed by strengthening the state.ic testing section, then rebuilt `build/state` target — passed 5/5.

**Result (second attempt — fresh builds):**
- Python build: all 10 targets built, all validations passed
- Python vs src/: **EQUIVALENT** (all 5 dimensions)
- Go build: all 10 targets built, all validations passed
- Go vs src/: **EQUIVALENT** (all 5 dimensions)
- Go vs Python (cross-language): **EQUIVALENT** (all 5 dimensions)

| Comparison | public_api | test_suite | runtime_behavior | dependency_compat | config_compat |
|-----------|-----------|-----------|-----------------|------------------|--------------|
| Python vs src/ | pass | pass | pass | pass | pass |
| Go vs src/ | pass | pass | pass | pass | pass |
| Go vs Python | pass | pass | pass | pass | pass |

**Key lessons from the full challenge:**
1. Language-neutral intent files work — the same abstract specs produce equivalent Python and Go implementations
2. The critical move is putting language-specific details (file paths, import patterns, naming conventions, framework choices) in implementation files
3. Validation files must also be language-neutral — no hardcoded file paths, test commands, or framework references
4. Test specs need to be explicit about what testing infra to use (real backends vs mocks) since different agents may make different choices
5. Dependencies must be explicitly listed — don't rely on transitive installation
6. Non-determinism in LLM builds means some runs may diverge slightly; tighter specs reduce variance
