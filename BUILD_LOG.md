# intentc Build Log

## Phase 1: Project Setup & Core Types ✓
- Created project skeleton: `pyproject.toml` with typer, pydantic, pyyaml, rich
- Installed uv, created virtualenv, installed all dependencies
- Created directory structure: `src/{core,parser,config,graph,git,agent,builder,cli,validation,state}/`
- Implemented `src/core/types.py` with all 12 foundation types:
  - Intent, Validation, ValidationType, ValidationFile
  - Target, TargetStatus, BuildResult, ValidationResult
  - AgentProfile, PromptTemplates, ToolConfig, SchemaViolation
  - Duration parsing/serialization helpers (_parse_duration, _serialize_duration)
- 19 core tests passing

## Phase 2: Wave 2 Packages (depends on core only) ✓
Dispatched 4 parallel agents:
- **parser** (79 tests) - ParseIntentFile, ParseValidationFile, TargetRegistry, schema validation (ValidateIntentSchema, ValidateProjectIntent, ValidateValidationSchema, ValidateAllSpecs), cross-file validation
- **config** (32 tests) - Config/BuildConfig/LoggingConfig types, LoadConfig, SaveConfig, MergeConfig, GetProfile, ValidateConfig, defaults
- **graph** (36 tests) - DAG with Node, AddTarget, Resolve, DetectCycles, TopologicalSort, GetAffected, GetDependencyChain, Visualize
- **git** (23 tests) - GitStatus, GitManager protocol, GitCLIManager, status parsing, commit constants

## Phase 3: Wave 3 Packages ✓
Dispatched 3 parallel agents:
- **agent** (44 tests) - Agent protocol, BuildContext, CLIAgent (with prompt construction, retry logic), ClaudeAgent (composition), CodexAgent, MockAgent, create_from_profile factory
- **state** (22 tests) - StateManager protocol, FileStateManager, JSON persistence in .intentc/state/, versioned schemas, forward compatibility
- **validation** (42 tests) - Validator protocol, FileCheckValidator, FolderCheckValidator, CommandCheckValidator, LLMJudgeValidator, Registry, Runner with RunReport

## Phase 4: Wave 4 Packages ✓
Dispatched 2 parallel agents:
- **builder** (31 tests) - Builder class, BuildOptions, full pipeline (schema validation → load targets → DAG → build set → resolve output → build), Clean, Validate, agent_factory injection
- **cli** (26 tests) - typer app with all commands: init, build, clean, validate, status, commit, check, add intent/validation, list intents/validations/profiles

## Fixes Applied
1. Fixed git status parsing bug - dead code after `continue` on line 113 of manager.py
2. Fixed builder tests - injected agent_factory to avoid creating real ClaudeAgent in tests
3. Fixed dependency_names in BuildContext - use intent.depends_on instead of target.dependencies
4. Fixed builder.validate() signature to match CLI expectations
5. Fixed CLI validate command to pass correct arguments to builder

## Test Results: 354 tests, ALL PASSING ✓

## Self-Compilation Verification ✓
- `intentc check` → "All spec files are valid." (validates all 10 .ic + 10 .icv + project.ic + config)
- `intentc status --tree` → Shows complete dependency tree with all 10 targets
- `intentc build --dry-run` → Plans build in correct dependency order (core → config/git/graph/parser → agent/state/validation → builder → cli)
- `intentc validate core -o .` → Passes folder_check (src/core exists) and command_check (pytest passes). LLM judge N/A in nested Claude session.
- `intentc validate parser -o .` → Same: deterministic checks pass, LLM judge N/A.
- `intentc list intents` → Lists all 10 features with dep/validation counts
- `intentc list validations` → Lists 4 validation types with descriptions
- `intentc list profiles` → Shows default claude profile

## Architecture
- 10 Python packages, ~4,500 lines of implementation
- Full dependency injection, no globals or singletons
- Pluggable agent system (Claude, Codex, generic CLI)
- Schema validation as Step 0 of every build/validate
- Per-target profile resolution (CLI flag > .ic profile > default)
- Comprehensive mock infrastructure for testing
- Self-describing: intentc successfully parses, validates, and plans builds of its own intent specs

---

## Reproducibility Test: src → src2 (Round 1)

### Build Execution
- Used Python `src/` intentc to build `./intent` into `src2/` via `intentc build --output src2 --force`
- Agent: Claude Code (`claude -p --output-format text --allowedTools Bash Read Write Edit Glob Grep`)

### Fixes to src/ Before Build
1. **System prompt too vague** - Added explicit file-writing instructions to `cli_agent.py` default system prompt
2. **Wrong tool names** - Fixed `config.py` default profile tools from `bash/file_read/file_write` to `Bash/Read/Write/Edit/Glob/Grep` (Claude Code's actual tool names)

### Build Results
- 5/10 targets marked "built": core, config, git, graph, state
- 4/10 targets marked "failed" (but files were generated): agent, parser, validation, builder
- 1/10 not started: cli (pending - building in follow-up)
- Failures were due to `claude -p` returning non-zero exit codes (rate limiting) after files were already written

### Deviation Analysis: src/ vs src2/

| Package | Verdict | File Match | API Match | Import Compat |
|---------|---------|:---:|:---:|:---:|
| core | **Identical** | 3/3 | YES | YES |
| config | **Identical** | 3/3 | YES | YES |
| graph | **Identical** | 3/3 | YES | YES |
| git | **Identical** | 3/3 | YES | YES |
| state | **Identical** | 3/3 | YES | YES |
| parser | **Identical** | 3/3 | YES | YES |
| agent | **BREAKING** | 7/7 (different names) | NO | NO |
| validation | **BREAKING** | 5 vs 10 files | NO | NO |
| builder | **Moderate** | 3/3 | Partial | NO |

### Key Deviations (Blocking Self-Hosting)

1. **agent: `base.py` → `interface.py` rename** - Import path `from agent.base import Agent, BuildContext` breaks
2. **agent: `validate_with_llm()` → `validate()`** - Protocol method renamed
3. **agent: `MockAgent` missing** - Test helper not generated
4. **validation: monolithic → split** - 4 files split into 9; different method names (`validator_type()` → `type()`)
5. **validation: internal inconsistency** - `validators.py` uses 3-arg `validate(ctx, validation, output_dir)` but `interface.py` defines 2-arg
6. **builder: return type changed** - `build()` returns `list[BuildResult]` instead of `None`
7. **builder: imports `agent.interface`** instead of `agent.base`

### Deviation Metrics
- **Packages fully reproduced**: 6/9 (67%)
- **Packages with breaking changes**: 3/9 (33%)
- **Total blocking API breaks**: 7
- **Self-hosting possible**: NO (import paths and method signatures incompatible)

---

## Project Validation Suite

Created behavioral acceptance tests in `intent/project_validation/`. These test CLI behavior (not internal structure) to measure functional equivalence between implementations.

### Examples (3)
1. **hello_world** - Trivial: single-feature CLI greeter
2. **todo_api** - Medium: 3-feature REST API (models → storage → api)
3. **multiplayer_pong** - Complex: 3-feature browser game (engine, networking, renderer)

### Workflows (5)
1. **01_check** - Spec validation: valid specs pass, corrupt specs caught
2. **02_status** - Status reporting: lists all targets, all initially pending
3. **03_dry_run** - Build dry-run: plans without side effects
4. **04_list** - List commands: intents, validations, profiles all work
5. **05_add_scaffold** - Add intent: creates scaffold, validates, rejects duplicates

### Results: src/ (Reference)
- **15/15 PASS** across all 3 examples × 5 workflows

### Next: Rebuild src2, run same suite, compare
See `SELF_COMPILATION.md` for the repeatable process.
