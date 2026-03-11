# Module Hierarchy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reorganize intent/ features into a nested module hierarchy with path-based dependency references (e.g., `core/parser`, `validation/*`), update the parser to support recursive discovery and glob expansion, and replace the editor's SVG DAG with a collapsible tree view showing upstream dependencies on click.

**Architecture:** The parser's `TargetRegistry` becomes the central change point: it walks `intent/` recursively to discover features, derives module-qualified names from directory paths, and expands `*` globs in `depends_on`. The DAG, builder, state manager, and editor all consume these path-based names as opaque strings. The editor frontend replaces its BFS-layered SVG with a DOM-based collapsible tree, with a dependency detail panel shown on feature selection.

**Tech Stack:** Python 3.11+ (pydantic, FastAPI, PyYAML), vanilla JS/HTML/CSS (no build step)

---

## New intent/ Directory Structure

```
intent/
├── project.ic
├── core/
│   ├── intent/intent.ic, validations.icv
│   ├── target/target.ic, validations.icv
│   ├── build_result/build_result.ic, validations.icv
│   ├── schema/schema.ic, validations.icv
│   ├── parser/parser.ic, validations.icv
│   └── graph/graph.ic, validations.icv
├── validation/
│   ├── types/types.ic, validations.icv
│   ├── runner/runner.ic, validations.icv
│   ├── file_check/file_check.ic, validations.icv
│   ├── folder_check/folder_check.ic, validations.icv
│   ├── command_check/command_check.ic, validations.icv
│   └── llm_judge/llm_judge.ic, validations.icv
├── agents/
│   ├── base/base.ic, validations.icv
│   ├── profiles/profiles.ic, validations.icv
│   ├── config/config.ic, validations.icv
│   ├── claude/claude.ic, validations.icv
│   └── codex/codex.ic, validations.icv
├── build/
│   ├── builder/builder.ic, validations.icv
│   ├── state/state.ic, validations.icv
│   └── git/git.ic, validations.icv
├── cli/
│   └── commands/commands.ic, validations.icv
└── editor/
    └── ui/ui.ic, validations.icv
```

---

## Task 1: Update Parser for Recursive Discovery and Glob Expansion

The parser is the foundation — all other changes depend on it.

**Files:**
- Modify: `src/parser/parser.py` (TargetRegistry.load_targets, validate_all_specs)
- Modify: `src/parser/test_parser.py` (add tests for nested targets, globs)

### Step 1: Write tests for recursive target discovery

Add to `src/parser/test_parser.py`:

```python
class TestNestedTargetRegistry:
    """Tests for recursive module-based target discovery."""

    def test_discovers_nested_targets(self, tmp_path: Path) -> None:
        """Registry finds targets in nested module directories."""
        root = str(tmp_path)
        _make_project_ic(root)
        # core/parser is a nested feature
        path = os.path.join(root, "intent", "core", "parser", "parser.ic")
        _write_file(path, "---\nname: core/parser\nversion: 1\n---\n# Parser\n")
        # core/graph is another
        path2 = os.path.join(root, "intent", "core", "graph", "graph.ic")
        _write_file(path2, "---\nname: core/graph\nversion: 1\n---\n# Graph\n")

        registry = TargetRegistry(project_root=root)
        registry.load_targets()
        names = [t.name for t in registry.get_all_targets()]
        assert "core/parser" in names
        assert "core/graph" in names

    def test_discovers_deeply_nested_targets(self, tmp_path: Path) -> None:
        """Registry finds targets 3+ levels deep."""
        root = str(tmp_path)
        _make_project_ic(root)
        path = os.path.join(root, "intent", "a", "b", "c", "c.ic")
        _write_file(path, "---\nname: a/b/c\nversion: 1\n---\n# C\n")

        registry = TargetRegistry(project_root=root)
        registry.load_targets()
        t = registry.get_target("a/b/c")
        assert t.name == "a/b/c"

    def test_module_dirs_without_ic_are_skipped(self, tmp_path: Path) -> None:
        """Directories that are pure modules (no .ic file) are not targets."""
        root = str(tmp_path)
        _make_project_ic(root)
        # core/ is a module dir (no .ic), core/parser/ is a feature
        os.makedirs(os.path.join(root, "intent", "core", "parser"))
        _write_file(
            os.path.join(root, "intent", "core", "parser", "parser.ic"),
            "---\nname: core/parser\nversion: 1\n---\n# P\n",
        )

        registry = TargetRegistry(project_root=root)
        registry.load_targets()
        names = [t.name for t in registry.get_all_targets()]
        assert "core/parser" in names
        assert "core" not in names  # module dir, not a target

    def test_top_level_targets_still_work(self, tmp_path: Path) -> None:
        """Root-level features (e.g., intent/core/core.ic) still work."""
        root = str(tmp_path)
        _make_project_ic(root)
        _make_ic(root, "core")

        registry = TargetRegistry(project_root=root)
        registry.load_targets()
        t = registry.get_target("core")
        assert t.name == "core"

    def test_nested_validations_discovered(self, tmp_path: Path) -> None:
        """Validation files in nested directories are found."""
        root = str(tmp_path)
        _make_project_ic(root)
        feat_dir = os.path.join(root, "intent", "core", "parser")
        os.makedirs(feat_dir)
        _write_file(
            os.path.join(feat_dir, "parser.ic"),
            "---\nname: core/parser\nversion: 1\n---\n# P\n",
        )
        _write_file(
            os.path.join(feat_dir, "validations.icv"),
            "---\ntarget: core/parser\nversion: 1\nvalidations:\n"
            "  - name: check\n    type: folder_check\n    path: src/parser\n---\n",
        )

        registry = TargetRegistry(project_root=root)
        registry.load_targets()
        t = registry.get_target("core/parser")
        assert len(t.validations) == 1
        assert t.validations[0].target == "core/parser"
```

### Step 2: Run tests to verify they fail

Run: `cd /Users/pboueri/repos/intentc/src && uv run pytest parser/test_parser.py::TestNestedTargetRegistry -v`
Expected: FAIL — `TestNestedTargetRegistry` class doesn't exist yet (tests just added), and the registry won't find nested targets.

### Step 3: Implement recursive target discovery in TargetRegistry

Replace the `load_targets` method in `src/parser/parser.py` (lines 278-331):

```python
def load_targets(self) -> None:
    """Walk the intent/ directory recursively, parse all .ic and .icv files, build target map.

    Features are identified by directories containing a .ic file.
    The target name is the relative path from intent/ to the feature directory,
    using '/' as separator. For example:
      intent/core/parser/parser.ic -> name "core/parser"
      intent/core/core.ic -> name "core"

    Raises:
        FileNotFoundError: If intent/ directory does not exist.
        ValueError: If any spec file has parse errors.
    """
    if not os.path.isdir(self._intent_dir):
        raise FileNotFoundError(
            f"parser: intent directory not found: {self._intent_dir}"
        )

    self._targets.clear()
    self._project_intent = None

    # Parse project.ic if it exists.
    project_ic_path = os.path.join(self._intent_dir, "project.ic")
    if os.path.isfile(project_ic_path):
        self._project_intent = ParseIntentFile(project_ic_path)

    # Walk the directory tree recursively.
    for dirpath, dirnames, filenames in os.walk(self._intent_dir):
        # Skip the intent root itself (project.ic lives there, not features).
        if os.path.abspath(dirpath) == os.path.abspath(self._intent_dir):
            continue

        # Look for .ic files in this directory.
        ic_files = sorted(f for f in filenames if f.endswith(".ic"))
        if not ic_files:
            continue

        # Derive target name from relative path.
        rel_path = os.path.relpath(dirpath, self._intent_dir)
        target_name = rel_path.replace(os.sep, "/")

        # Parse the .ic file.
        ic_path = os.path.join(dirpath, ic_files[0])
        intent = ParseIntentFile(ic_path)

        # Collect all .icv files in the directory.
        icv_files = sorted(f for f in filenames if f.endswith(".icv"))
        validation_files: list[ValidationFile] = []
        for icv_file in icv_files:
            icv_path = os.path.join(dirpath, icv_file)
            vf = ParseValidationFile(icv_path)
            validation_files.append(vf)

        target = Target(
            name=target_name,
            intent=intent,
            validations=validation_files,
        )
        self._targets[target_name] = target
```

### Step 4: Run tests to verify they pass

Run: `cd /Users/pboueri/repos/intentc/src && uv run pytest parser/test_parser.py::TestNestedTargetRegistry -v`
Expected: PASS

### Step 5: Write tests for glob expansion

Add to `src/parser/test_parser.py`:

```python
from parser.parser import expand_dependency_globs


class TestExpandDependencyGlobs:
    """Tests for wildcard expansion in depends_on."""

    def test_no_globs_passthrough(self) -> None:
        known = {"core", "core/parser", "core/graph"}
        result = expand_dependency_globs(["core", "core/parser"], known)
        assert result == ["core", "core/parser"]

    def test_star_expands_direct_children(self) -> None:
        known = {"core/parser", "core/graph", "core/intent", "agents/base"}
        result = expand_dependency_globs(["core/*"], known)
        assert sorted(result) == ["core/graph", "core/intent", "core/parser"]

    def test_star_does_not_expand_grandchildren(self) -> None:
        known = {"a/b/c", "a/x"}
        result = expand_dependency_globs(["a/*"], known)
        assert result == ["a/x"]  # a/b/c is not a direct child of a/

    def test_mixed_globs_and_literals(self) -> None:
        known = {"core/parser", "core/graph", "agents/base"}
        result = expand_dependency_globs(["core/*", "agents/base"], known)
        assert sorted(result) == ["agents/base", "core/graph", "core/parser"]

    def test_unknown_glob_returns_empty(self) -> None:
        known = {"core/parser"}
        result = expand_dependency_globs(["nonexistent/*"], known)
        assert result == []

    def test_deduplication(self) -> None:
        known = {"core/parser", "core/graph"}
        result = expand_dependency_globs(["core/*", "core/parser"], known)
        assert sorted(result) == ["core/graph", "core/parser"]
```

### Step 6: Run tests to verify they fail

Run: `cd /Users/pboueri/repos/intentc/src && uv run pytest parser/test_parser.py::TestExpandDependencyGlobs -v`
Expected: FAIL — `expand_dependency_globs` doesn't exist yet.

### Step 7: Implement expand_dependency_globs

Add to `src/parser/parser.py` after the imports:

```python
def expand_dependency_globs(
    deps: list[str], known_targets: set[str]
) -> list[str]:
    """Expand wildcard patterns in dependency lists.

    Supports:
      - "module/*" -> all targets whose parent module is "module"
        (direct children only, not grandchildren)
      - Literal names are passed through unchanged.

    Returns a deduplicated list preserving order of first occurrence.
    """
    result: list[str] = []
    seen: set[str] = set()

    for dep in deps:
        if dep.endswith("/*"):
            prefix = dep[:-2]  # e.g., "core" from "core/*"
            for name in sorted(known_targets):
                # Direct child: starts with prefix/ and has no further /
                if name.startswith(prefix + "/"):
                    remainder = name[len(prefix) + 1 :]
                    if "/" not in remainder and name not in seen:
                        seen.add(name)
                        result.append(name)
        else:
            if dep not in seen:
                seen.add(dep)
                result.append(dep)

    return result
```

### Step 8: Run tests to verify they pass

Run: `cd /Users/pboueri/repos/intentc/src && uv run pytest parser/test_parser.py::TestExpandDependencyGlobs -v`
Expected: PASS

### Step 9: Update validate_all_specs for nested directories and glob deps

Replace the `validate_all_specs` function in `src/parser/parser.py` (lines 646-807). The key changes:
1. Use `os.walk` instead of `os.listdir` to find features recursively.
2. Derive `dir_name` as a `/`-separated path relative to `intent/`.
3. Cross-file validation of `depends_on` must expand globs before checking.
4. The `name` field must match the derived path, not just the leaf directory name.
5. The `target` field in `.icv` files must match the derived path.

```python
def validate_all_specs(project_root: str) -> list[SchemaViolation]:
    """Walk the intent/ directory and validate all spec files.

    Performs both per-file schema validation and cross-file consistency checks.
    Supports nested module directories (e.g., intent/core/parser/).

    Returns an aggregate list of all violations found.
    """
    abs_root = os.path.abspath(project_root)
    intent_dir = os.path.join(abs_root, "intent")

    violations: list[SchemaViolation] = []

    if not os.path.isdir(intent_dir):
        violations.append(
            SchemaViolation(
                file_path=intent_dir,
                field="",
                message=f"parser: intent directory not found: {intent_dir}",
                severity="error",
            )
        )
        return violations

    # Track feature names for duplicate detection.
    feature_names: set[str] = set()
    # Track all discovered feature paths for depends_on validation.
    feature_paths: set[str] = set()
    # Collect intents for cross-file validation.
    feature_intents: dict[str, Intent] = {}  # path -> Intent

    # --- Validate project.ic ---
    project_ic_path = os.path.join(intent_dir, "project.ic")
    if os.path.isfile(project_ic_path):
        try:
            project_intent = ParseIntentFile(project_ic_path)
            violations.extend(validate_project_intent(project_intent))
        except (ValueError, FileNotFoundError) as e:
            violations.append(
                SchemaViolation(
                    file_path=project_ic_path,
                    field="",
                    message=str(e),
                    severity="error",
                )
            )

    # --- Walk feature directories recursively ---
    for dirpath, dirnames, filenames in os.walk(intent_dir):
        if os.path.abspath(dirpath) == os.path.abspath(intent_dir):
            continue

        ic_files = sorted(f for f in filenames if f.endswith(".ic"))
        if not ic_files:
            continue

        # Derive the feature path (e.g., "core/parser").
        rel = os.path.relpath(dirpath, intent_dir)
        feature_path = rel.replace(os.sep, "/")
        feature_paths.add(feature_path)

        # Parse the .ic file.
        ic_path = os.path.join(dirpath, ic_files[0])
        try:
            intent = ParseIntentFile(ic_path)
            intent_violations = validate_intent_schema(intent)
            violations.extend(intent_violations)

            feature_intents[feature_path] = intent

            # Cross-file: name must match derived path.
            if intent.name != feature_path:
                violations.append(
                    SchemaViolation(
                        file_path=ic_path,
                        field="name",
                        message=(
                            f"parser: {ic_path}: name '{intent.name}' does not match "
                            f"directory path '{feature_path}'"
                        ),
                        severity="error",
                    )
                )

            # Duplicate feature name detection.
            if intent.name in feature_names:
                violations.append(
                    SchemaViolation(
                        file_path=ic_path,
                        field="name",
                        message=f"parser: {ic_path}: duplicate feature name '{intent.name}'",
                        severity="error",
                    )
                )
            feature_names.add(intent.name)

        except (ValueError, FileNotFoundError) as e:
            violations.append(
                SchemaViolation(
                    file_path=ic_path,
                    field="",
                    message=str(e),
                    severity="error",
                )
            )

        # Parse .icv files.
        icv_files = sorted(f for f in filenames if f.endswith(".icv"))
        for icv_file in icv_files:
            icv_path = os.path.join(dirpath, icv_file)
            try:
                vf = ParseValidationFile(icv_path)
                vf_violations = validate_validation_schema(vf)
                violations.extend(vf_violations)

                # Cross-file: .icv target must match feature path.
                if vf.target != feature_path:
                    violations.append(
                        SchemaViolation(
                            file_path=icv_path,
                            field="target",
                            message=(
                                f"parser: {icv_path}: target '{vf.target}' does not match "
                                f"directory path '{feature_path}'"
                            ),
                            severity="error",
                        )
                    )

            except (ValueError, FileNotFoundError) as e:
                violations.append(
                    SchemaViolation(
                        file_path=icv_path,
                        field="",
                        message=str(e),
                        severity="error",
                    )
                )

    # --- Cross-file: depends_on references must exist (after glob expansion) ---
    for feature_path, intent in feature_intents.items():
        expanded = expand_dependency_globs(intent.depends_on, feature_paths)
        for dep in expanded:
            if dep not in feature_paths:
                violations.append(
                    SchemaViolation(
                        file_path=intent.file_path,
                        field="depends_on",
                        message=(
                            f"parser: {intent.file_path}: depends_on references "
                            f"unknown feature '{dep}'"
                        ),
                        severity="error",
                    )
                )

    return violations
```

### Step 10: Run the full parser test suite

Run: `cd /Users/pboueri/repos/intentc/src && uv run pytest parser/test_parser.py -v`
Expected: Some existing tests may fail due to the change from `os.listdir` to `os.walk`. Fix any that break (primarily tests that check `name != dir_name` — the error message now says "directory path" instead of "directory name").

### Step 11: Fix any broken existing parser tests

The existing tests use flat directories like `intent/auth/auth.ic` with name `auth`. These should still work because `os.walk` finds them the same way. The error message wording changed from "directory name" to "directory path" — update assertions that match exact error strings.

### Step 12: Commit

```bash
git add src/parser/parser.py src/parser/test_parser.py
git commit -m "feat: support recursive module directories and glob expansion in parser"
```

---

## Task 2: Update State Manager for Path-Based Target Names

Target names now contain `/` (e.g., `core/parser`). The state manager stores build results in directories named by target, so we need to handle `/` in target names safely.

**Files:**
- Modify: `src/state/manager.py` (lines 125-132, _target_dir and related methods)
- Modify: `src/state/test_state.py`

### Step 1: Write test for path-based target names in state

Add to `src/state/test_state.py`:

```python
class TestPathBasedTargetNames:
    """Test that target names containing '/' work correctly."""

    def test_save_and_load_nested_target(self, tmp_path: Path) -> None:
        sm = FileStateManager(str(tmp_path))
        sm.initialize()
        sm.set_output_dir(str(tmp_path / "output"))

        result = BuildResult(
            target="core/parser",
            generation_id="gen-123",
            success=True,
            files=["src/parser.py"],
            output_dir=str(tmp_path / "output"),
        )
        sm.save_build_result(result)
        loaded = sm.get_latest_build_result("core/parser")
        assert loaded.target == "core/parser"
        assert loaded.success is True

    def test_status_with_slashes(self, tmp_path: Path) -> None:
        sm = FileStateManager(str(tmp_path))
        sm.initialize()
        sm.set_output_dir(str(tmp_path / "output"))

        sm.update_target_status("core/parser", TargetStatus.BUILT)
        assert sm.get_target_status("core/parser") == TargetStatus.BUILT

    def test_reset_nested_target(self, tmp_path: Path) -> None:
        sm = FileStateManager(str(tmp_path))
        sm.initialize()
        sm.set_output_dir(str(tmp_path / "output"))

        sm.update_target_status("build/state", TargetStatus.BUILT)
        sm.reset_target("build/state")
        assert sm.get_target_status("build/state") == TargetStatus.PENDING
```

### Step 2: Run tests to verify behavior

Run: `cd /Users/pboueri/repos/intentc/src && uv run pytest state/test_state.py::TestPathBasedTargetNames -v`
Expected: May pass already since status.json uses the name as a dict key (no filesystem issue). The `_target_dir` method may create nested dirs due to `/` — check if that's a problem.

### Step 3: Fix _target_dir to sanitize slashes

In `src/state/manager.py`, update `_target_dir` to replace `/` with `--` so that `core/parser` becomes `core--parser` as a directory name:

```python
def _target_dir(self, target_name: str) -> str:
    # Replace '/' with '--' to create flat, safe directory names
    safe_name = target_name.replace("/", "--")
    return os.path.join(self._builds_dir, safe_name)
```

### Step 4: Run all state tests

Run: `cd /Users/pboueri/repos/intentc/src && uv run pytest state/ -v`
Expected: PASS

### Step 5: Commit

```bash
git add src/state/manager.py src/state/test_state.py
git commit -m "feat: handle path-based target names in state manager"
```

---

## Task 3: Reorganize intent/ Directory

Move all existing intent files into the new module hierarchy and update their frontmatter.

**Files:**
- Move + modify: All files under `intent/`

### Step 1: Create the new directory structure and move files

This is a scripted operation. Create a shell script or do it manually. The mapping is:

| Old Location | New Location | New `name` | New `depends_on` |
|---|---|---|---|
| `intent/core/` | Split into 4 features below | — | — |
| — | `intent/core/intent/` | `core/intent` | (none) |
| — | `intent/core/target/` | `core/target` | `[core/intent]` |
| — | `intent/core/build_result/` | `core/build_result` | `[core/target]` |
| — | `intent/core/schema/` | `core/schema` | `[core/intent]` |
| `intent/parser/` | `intent/core/parser/` | `core/parser` | `[core/intent, core/target, core/schema]` |
| `intent/graph/` | `intent/core/graph/` | `core/graph` | `[core/target]` |
| `intent/validation/` | Split into 6 features below | — | — |
| — | `intent/validation/types/` | `validation/types` | `[core/intent]` |
| — | `intent/validation/runner/` | `validation/runner` | `[validation/types, agents/base]` |
| — | `intent/validation/file_check/` | `validation/file_check` | `[validation/types]` |
| — | `intent/validation/folder_check/` | `validation/folder_check` | `[validation/types]` |
| — | `intent/validation/command_check/` | `validation/command_check` | `[validation/types]` |
| — | `intent/validation/llm_judge/` | `validation/llm_judge` | `[validation/types, agents/base]` |
| `intent/agent/` | Split into 5 features below | — | — |
| — | `intent/agents/base/` | `agents/base` | `[core/intent, core/target]` |
| — | `intent/agents/profiles/` | `agents/profiles` | `[core/intent]` |
| — | `intent/agents/config/` | `agents/config` | `[agents/profiles]` |
| — | `intent/agents/claude/` | `agents/claude` | `[agents/base, agents/config]` |
| — | `intent/agents/codex/` | `agents/codex` | `[agents/base, agents/config]` |
| `intent/config/` | `intent/agents/config/` | `agents/config` | `[agents/profiles]` |
| `intent/state/` | `intent/build/state/` | `build/state` | `[core/target, core/build_result, build/git]` |
| `intent/git/` | `intent/build/git/` | `build/git` | `[core/*]` |
| `intent/builder/` | `intent/build/builder/` | `build/builder` | `[core/*, validation/*, agents/*, build/state]` |
| `intent/cli/` | `intent/cli/commands/` | `cli/commands` | `[core/*, validation/*, agents/*, build/*, editor/ui]` |
| `intent/editor/` | `intent/editor/ui/` | `editor/ui` | `[core/*, build/builder, agents/config]` |

### Step 2: Execute the move and split

Run a bash script to create new directories, write new .ic/.icv files. For the **split** features (core -> 4 features, validation -> 6 features, agent -> 5 features), we need to write new .ic files that contain the relevant subset of the original spec. For **moved** features (git, state, builder, cli, editor), we move the file and update frontmatter.

**Core split details:**

**`intent/core/intent/intent.ic`** — Extract Intent type + constraints from core.ic:
```yaml
---
name: core/intent
version: 1
tags: [foundation]
---
```
Body: Intent type, ValidationType enum, Validation type, ValidationFile type from current core.ic.

**`intent/core/target/target.ic`** — Extract Target, TargetStatus:
```yaml
---
name: core/target
version: 1
depends_on: [core/intent]
tags: [foundation]
---
```

**`intent/core/build_result/build_result.ic`** — Extract BuildResult, ValidationResult:
```yaml
---
name: core/build_result
version: 1
depends_on: [core/target]
tags: [foundation]
---
```

**`intent/core/schema/schema.ic`** — Extract SchemaViolation:
```yaml
---
name: core/schema
version: 1
depends_on: [core/intent]
tags: [foundation]
---
```

For each new feature, create a corresponding `validations.icv` with a subset of the original validations relevant to that feature.

**Moved features** — For each: update `name:` and `depends_on:` in frontmatter, update `target:` in .icv files.

### Step 3: Delete old directories

Remove `intent/core/core.ic`, `intent/config/`, `intent/parser/`, `intent/graph/`, `intent/git/`, `intent/agent/`, `intent/state/`, `intent/validation/`, `intent/builder/`, `intent/cli/`, `intent/editor/` (the old flat versions).

### Step 4: Verify with parser

Run: `cd /Users/pboueri/repos/intentc/src && uv run python -c "from parser.parser import TargetRegistry; r = TargetRegistry('..'); r.load_targets(); print([t.name for t in r.get_all_targets()])"`
Expected: All new path-based target names are listed.

### Step 5: Commit

```bash
git add intent/
git commit -m "feat: reorganize intent/ into module hierarchy with path-based names"
```

---

## Task 4: Update DAG Resolve for Glob Expansion

The DAG's `resolve()` reads `intent.depends_on` which may now contain globs like `core/*`. These need to be expanded before edge resolution.

**Files:**
- Modify: `src/graph/dag.py` (resolve method)
- Modify: `src/graph/test_graph.py`

### Step 1: Write test for glob expansion in DAG

Add to `src/graph/test_graph.py`:

```python
class TestDAGWithGlobs:
    def test_resolve_expands_globs(self) -> None:
        from core.types import Intent, Target
        from parser.parser import expand_dependency_globs

        t1 = Target(name="core/parser", intent=Intent(name="core/parser", depends_on=[]))
        t2 = Target(name="core/graph", intent=Intent(name="core/graph", depends_on=[]))
        t3 = Target(name="build/builder", intent=Intent(name="build/builder", depends_on=["core/*"]))

        dag = DAG()
        dag.add_target(t1)
        dag.add_target(t2)
        dag.add_target(t3)
        dag.resolve()

        # build/builder should depend on both core/parser and core/graph
        builder_node = dag.nodes["build/builder"]
        dep_names = sorted(d.name for d in builder_node.dependencies)
        assert dep_names == ["core/graph", "core/parser"]
```

### Step 2: Run to verify it fails

Run: `cd /Users/pboueri/repos/intentc/src && uv run pytest graph/test_graph.py::TestDAGWithGlobs -v`
Expected: FAIL — resolve() tries to look up "core/*" as a literal node name.

### Step 3: Update DAG.resolve() to expand globs

In `src/graph/dag.py`, update the `resolve` method:

```python
def resolve(self) -> None:
    """Resolve dependency edges from each target's intent.depends_on.

    Expands glob patterns (e.g., "core/*") before resolving edges.
    After resolution, updates self.roots to contain all nodes with no
    dependencies.

    Raises DAGError for unknown dependencies or self-dependencies.
    """
    from parser.parser import expand_dependency_globs

    known_names = set(self.nodes.keys())

    for node in self.nodes.values():
        raw_deps = node.target.intent.depends_on
        expanded_deps = expand_dependency_globs(raw_deps, known_names)
        for dep_name in expanded_deps:
            if dep_name == node.name:
                raise DAGError(
                    f"graph: target '{node.name}' depends on itself"
                )
            if dep_name not in self.nodes:
                raise DAGError(
                    f"graph: target '{node.name}' depends on unknown target '{dep_name}'"
                )
            dep_node = self.nodes[dep_name]
            if dep_node not in node.dependencies:
                node.dependencies.append(dep_node)
            if node not in dep_node.dependents:
                dep_node.dependents.append(node)

    self.roots = [n for n in self.nodes.values() if not n.dependencies]
```

### Step 4: Run all graph tests

Run: `cd /Users/pboueri/repos/intentc/src && uv run pytest graph/test_graph.py -v`
Expected: PASS

### Step 5: Commit

```bash
git add src/graph/dag.py src/graph/test_graph.py
git commit -m "feat: expand glob dependencies in DAG resolve"
```

---

## Task 5: Update Editor API for Tree Structure and Path-Based Names

**Files:**
- Modify: `src/editor/api.py`
- Modify: `src/editor/watcher.py`
- Modify: `src/editor/test_editor.py`

### Step 1: Update GET /api/dag to return tree structure

Add a `_build_tree` helper and update `get_dag()` in `src/editor/api.py`:

```python
def _build_tree(nodes: list[dict]) -> dict:
    """Build a nested tree from flat node list with path-based names."""
    root = {"name": "root", "type": "module", "children": []}

    for node in nodes:
        parts = node["name"].split("/")
        current = root
        # Navigate/create module nodes for each path segment except last
        for i, part in enumerate(parts[:-1]):
            existing = None
            for child in current["children"]:
                if child["name"] == part and child.get("type") == "module":
                    existing = child
                    break
            if existing is None:
                new_module = {"name": part, "type": "module", "children": []}
                current["children"].append(new_module)
                existing = new_module
            current = existing

        # Add the feature node as a leaf
        leaf = {
            "name": parts[-1],
            "type": "feature",
            "path": node["name"],
            "status": node["status"],
            "depends_on": node["depends_on"],
            "tags": node.get("tags", []),
        }
        current["children"].append(leaf)

    # Sort children at each level
    def sort_tree(node):
        if "children" in node:
            node["children"].sort(key=lambda c: (c.get("type") != "module", c["name"]))
            for child in node["children"]:
                sort_tree(child)

    sort_tree(root)
    return root
```

Update `get_dag()` to include the tree:

```python
@router.get("/dag")
def get_dag():
    """Return the full DAG structure with nodes, edges, and tree."""
    # ... existing code ...
    tree = _build_tree(nodes)
    return {"nodes": nodes, "edges": edges, "tree": tree}
```

### Step 2: Add upstream dependencies endpoint

Add to `src/editor/api.py`:

```python
@router.get("/targets/{name:path}/upstream")
def get_upstream(name: str):
    """Return all transitive upstream dependencies for a target."""
    from editor.server import get_project_path
    from parser.parser import TargetRegistry
    from graph.dag import DAG

    project_path = get_project_path()
    registry = TargetRegistry(project_root=project_path)
    registry.load_targets()

    targets = registry.get_all_targets()
    dag = DAG()
    for t in targets:
        dag.add_target(t)
    dag.resolve()

    try:
        chain = dag.get_dependency_chain(name)
        # Remove the target itself from its chain
        upstream = [t.name for t in chain if t.name != name]
        return {"target": name, "upstream": upstream}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
```

### Step 3: Update target routes to accept path-based names

All existing routes like `/targets/{name}` need to accept names with `/` in them. FastAPI supports this with `{name:path}`. Update all target routes:

Replace all occurrences of `{name}` with `{name:path}` in the route decorators in `api.py`:

```python
@router.get("/targets/{name:path}")
@router.put("/targets/{name:path}/spec")
@router.put("/targets/{name:path}/validation")
@router.get("/targets/{name:path}/builds")
@router.post("/targets/{name:path}/build")
@router.post("/targets/{name:path}/clean")
@router.post("/targets/{name:path}/validate")
```

### Step 4: Update watcher for nested paths

In `src/editor/watcher.py`, the `file_changed` path detection needs to handle nested module directories. The watcher already sends relative paths — no change needed to the watcher itself, but the frontend's path matching (Task 6) will need updating.

### Step 5: Update editor tests

Update `src/editor/test_editor.py` to use nested test fixtures:

Update the `project_dir` fixture to include a nested target structure:

```python
@pytest.fixture
def project_dir():
    """Create a temporary intentc project with spec files."""
    import subprocess
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init"], cwd=d, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=d, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, capture_output=True)
    # ... config setup same as before ...

    # intent/project.ic - same as before

    # intent/core/intent/intent.ic (nested)
    core_intent_dir = os.path.join(intent_dir, "core", "intent")
    os.makedirs(core_intent_dir)
    with open(os.path.join(core_intent_dir, "intent.ic"), "w") as f:
        f.write(
            "---\n"
            "name: core/intent\n"
            "version: 1\n"
            "depends_on: []\n"
            "tags: [foundation]\n"
            "---\n\n"
            "# Intent Type\n\n"
            "Core intent type.\n"
        )

    # intent/core/intent/validations.icv
    with open(os.path.join(core_intent_dir, "validations.icv"), "w") as f:
        f.write(
            "---\n"
            "target: core/intent\n"
            "version: 1\n"
            "validations:\n"
            "  - name: intent-exists\n"
            "    type: folder_check\n"
            "    path: src/core\n"
            "---\n\n"
            "# Intent Validations\n"
        )

    # intent/core/parser/parser.ic (nested, depends on core/intent)
    parser_dir = os.path.join(intent_dir, "core", "parser")
    os.makedirs(parser_dir)
    with open(os.path.join(parser_dir, "parser.ic"), "w") as f:
        f.write(
            "---\n"
            "name: core/parser\n"
            "version: 1\n"
            "depends_on: [core/intent]\n"
            "tags: [parsing]\n"
            "---\n\n"
            "# Parser\n\n"
            "Parses spec files.\n"
        )

    yield d
    shutil.rmtree(d)
```

Update test assertions to use path-based names (e.g., `"core/intent"` instead of `"core"`, `"core/parser"` instead of `"parser"`).

### Step 6: Add tests for tree structure and upstream deps

```python
class TestGetDagTree:
    def test_returns_tree_structure(self, client):
        res = client.get("/api/dag")
        data = res.json()
        assert "tree" in data
        tree = data["tree"]
        assert tree["type"] == "module"
        # core module should exist with children
        core_mod = next(c for c in tree["children"] if c["name"] == "core")
        assert core_mod["type"] == "module"
        child_names = [c["name"] for c in core_mod["children"]]
        assert "intent" in child_names
        assert "parser" in child_names


class TestGetUpstream:
    def test_returns_upstream_deps(self, client):
        res = client.get("/api/targets/core/parser/upstream")
        assert res.status_code == 200
        data = res.json()
        assert data["target"] == "core/parser"
        assert "core/intent" in data["upstream"]

    def test_root_has_no_upstream(self, client):
        res = client.get("/api/targets/core/intent/upstream")
        assert res.status_code == 200
        data = res.json()
        assert data["upstream"] == []
```

### Step 7: Run editor tests

Run: `cd /Users/pboueri/repos/intentc/src && uv run pytest editor/test_editor.py -v`
Expected: PASS

### Step 8: Commit

```bash
git add src/editor/api.py src/editor/watcher.py src/editor/test_editor.py
git commit -m "feat: add tree structure to DAG API and upstream deps endpoint"
```

---

## Task 6: Replace SVG DAG with Collapsible Tree in Frontend

**Files:**
- Modify: `src/editor/static/index.html`
- Modify: `src/editor/static/app.js`
- Modify: `src/editor/static/styles.css`

### Step 1: Update HTML - Replace SVG with tree container

In `src/editor/static/index.html`, replace the DAG pane content:

```html
<div id="dag-pane" class="pane">
    <div class="pane-header">Modules</div>
    <div id="dag-container">
        <div id="module-tree"></div>
    </div>
</div>
```

### Step 2: Replace renderDag with renderTree in app.js

Replace the entire `renderDag` function and update `loadDag` in `src/editor/static/app.js`:

```javascript
function renderDag(data) {
    const container = document.getElementById("module-tree");
    container.innerHTML = "";

    if (!data.tree || !data.tree.children) return;

    // Store flat node data for upstream lookups
    dagData = data;

    function createTreeNode(node, depth) {
        const div = document.createElement("div");
        div.className = "tree-item";
        div.style.paddingLeft = (depth * 16) + "px";

        if (node.type === "module") {
            // Module: collapsible header
            const header = document.createElement("div");
            header.className = "tree-module";

            const toggle = document.createElement("span");
            toggle.className = "tree-toggle";
            toggle.textContent = "\u25BC"; // ▼
            header.appendChild(toggle);

            const label = document.createElement("span");
            label.className = "tree-module-label";
            label.textContent = node.name;
            header.appendChild(label);

            div.appendChild(header);

            const childContainer = document.createElement("div");
            childContainer.className = "tree-children";

            if (node.children) {
                node.children.forEach(child => {
                    childContainer.appendChild(createTreeNode(child, depth + 1));
                });
            }
            div.appendChild(childContainer);

            // Toggle collapse
            header.addEventListener("click", (e) => {
                e.stopPropagation();
                const isCollapsed = childContainer.classList.toggle("collapsed");
                toggle.textContent = isCollapsed ? "\u25B6" : "\u25BC"; // ▶ or ▼
            });
        } else {
            // Feature: clickable leaf node
            const leaf = document.createElement("div");
            leaf.className = "tree-feature" + (selectedTarget === node.path ? " selected" : "");
            leaf.setAttribute("data-path", node.path);

            const dot = document.createElement("span");
            dot.className = "tree-status status-" + (node.status || "pending");
            leaf.appendChild(dot);

            const label = document.createElement("span");
            label.className = "tree-feature-label";
            label.textContent = node.name;
            leaf.appendChild(label);

            leaf.addEventListener("click", (e) => {
                e.stopPropagation();
                selectTarget(node.path);
            });

            div.appendChild(leaf);
        }

        return div;
    }

    data.tree.children.forEach(child => {
        container.appendChild(createTreeNode(child, 0));
    });
}
```

### Step 3: Update selectTarget to show upstream dependencies

Update `selectTarget` in `app.js`:

```javascript
async function selectTarget(name) {
    selectedTarget = name;
    document.getElementById("editor-target-name").textContent = name;

    // Update tree selection visual
    document.querySelectorAll(".tree-feature").forEach(el => {
        el.classList.toggle("selected", el.getAttribute("data-path") === name);
    });

    // Fetch target details and upstream deps in parallel
    try {
        const [targetRes, upstreamRes] = await Promise.all([
            fetch(`/api/targets/${encodeURIComponent(name)}`),
            fetch(`/api/targets/${encodeURIComponent(name)}/upstream`),
        ]);

        if (!targetRes.ok) {
            document.getElementById("editor-content").innerHTML = `<p class="placeholder">Target not found</p>`;
            return;
        }

        const target = await targetRes.json();
        const upstream = upstreamRes.ok ? await upstreamRes.json() : { upstream: [] };
        renderEditor(target, upstream.upstream);
    } catch (e) {
        console.error("Failed to load target:", e);
    }
}
```

### Step 4: Update renderEditor to show upstream deps

Update `renderEditor` in `app.js` to accept and display upstream dependencies:

```javascript
function renderEditor(target, upstream) {
    const container = document.getElementById("editor-content");
    let html = "";

    // Action bar (same as before)
    html += `<div class="action-bar">
        <button class="action-btn action-build" onclick="triggerAction('build', '${target.name}', this)">Build</button>
        <button class="action-btn action-clean" onclick="triggerAction('clean', '${target.name}', this)">Clean</button>
        <button class="action-btn action-validate" onclick="triggerAction('validate', '${target.name}', this)">Validate</button>
    </div>`;

    // Upstream dependencies section
    if (upstream && upstream.length > 0) {
        html += `<div class="upstream-deps">
            <div class="upstream-header">Upstream Dependencies</div>
            <div class="upstream-chain">`;
        upstream.forEach((dep, i) => {
            html += `<span class="upstream-dep" onclick="selectTarget('${dep}')">${dep}</span>`;
            if (i < upstream.length - 1) html += `<span class="upstream-arrow">\u2192</span>`;
        });
        html += `</div></div>`;
    }

    // Status section (same as before)
    // ... rest of renderEditor stays the same ...
```

### Step 5: Update WebSocket file_changed handler for nested paths

In the `changesWs.onmessage` handler in `app.js`, update the path matching:

```javascript
} else if (msg.type === "file_changed") {
    loadDag();
    // Check if the changed file belongs to the selected target's module path
    if (selectedTarget && msg.path) {
        // Convert "intent/core/parser/parser.ic" to check if it matches "core/parser"
        const intentPrefix = "intent/";
        let relPath = msg.path;
        if (relPath.startsWith(intentPrefix)) {
            relPath = relPath.substring(intentPrefix.length);
        }
        // Remove the filename, keep the directory path
        const lastSlash = relPath.lastIndexOf("/");
        const dirPath = lastSlash >= 0 ? relPath.substring(0, lastSlash) : relPath;
        if (dirPath === selectedTarget) {
            selectTarget(selectedTarget);
        }
    }
}
```

### Step 6: Update CSS for tree styles

Add to `src/editor/static/styles.css`:

```css
/* Module Tree */
#module-tree {
    padding: 8px;
}

.tree-item {
    user-select: none;
}

.tree-module {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 4px 8px;
    cursor: pointer;
    border-radius: 4px;
    font-size: 12px;
    color: var(--text-dim);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}

.tree-module:hover {
    background: var(--surface);
}

.tree-toggle {
    font-size: 8px;
    width: 12px;
    text-align: center;
    flex-shrink: 0;
}

.tree-module-label {
    flex: 1;
}

.tree-children.collapsed {
    display: none;
}

.tree-feature {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 8px;
    cursor: pointer;
    border-radius: 4px;
    font-size: 12px;
    transition: background 0.15s;
}

.tree-feature:hover {
    background: var(--surface);
}

.tree-feature.selected {
    background: var(--surface);
    border-left: 2px solid var(--accent);
}

.tree-status {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
}

.tree-status.status-pending { background: var(--text-dim); }
.tree-status.status-building { background: var(--yellow); }
.tree-status.status-built { background: var(--green); }
.tree-status.status-failed { background: var(--red); }
.tree-status.status-outdated { background: var(--yellow); }

.tree-feature-label {
    color: var(--text);
}

/* Upstream Dependencies */
.upstream-deps {
    margin-bottom: 12px;
    padding: 8px;
    background: var(--surface);
    border-radius: 4px;
    border-left: 2px solid var(--accent);
}

.upstream-header {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-dim);
    margin-bottom: 6px;
}

.upstream-chain {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px;
    font-size: 11px;
}

.upstream-dep {
    padding: 2px 6px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 3px;
    cursor: pointer;
    color: var(--text);
    transition: border-color 0.15s;
}

.upstream-dep:hover {
    border-color: var(--accent);
    color: var(--accent);
}

.upstream-arrow {
    color: var(--text-dim);
    font-size: 10px;
}
```

### Step 7: Remove old SVG-specific CSS

Remove the following CSS rules from `styles.css` that are no longer needed:
- `.dag-node`, `.dag-node rect`, `.dag-node:hover rect`, `.dag-node.selected rect`, `.dag-node text`, `.dag-node .status-dot`
- `.dag-edge`

### Step 8: Commit

```bash
git add src/editor/static/
git commit -m "feat: replace SVG DAG with collapsible tree view and upstream deps"
```

---

## Task 7: Update Builder for Glob-Expanded Dependencies

**Files:**
- Modify: `src/builder/builder.py` (clean method dependency checking)

### Step 1: Update clean method

In `src/builder/builder.py`, the `clean` method at line 308 checks `if target in t.intent.depends_on` to find dependents. With globs, this needs to use the DAG's resolved edges instead:

```python
# Mark dependents as outdated
try:
    registry = TargetRegistry(self.project_root)
    registry.load_targets()
    dag = DAG()
    for t in registry.get_all_targets():
        dag.add_target(t)
    dag.resolve()
    affected = dag.get_affected(target)
    for t in affected:
        self.state_manager.update_target_status(
            t.name, TargetStatus.OUTDATED
        )
except Exception:
    pass
```

### Step 2: Run builder tests

Run: `cd /Users/pboueri/repos/intentc/src && uv run pytest builder/ -v`
Expected: PASS

### Step 3: Commit

```bash
git add src/builder/builder.py
git commit -m "fix: use DAG-resolved deps in clean's dependent detection"
```

---

## Task 8: Update Remaining Tests and Run Full Suite

**Files:**
- Modify: `src/parser/test_parser.py` (fix any broken tests from wording changes)
- Modify: `src/editor/test_editor.py` (update for nested target structure)

### Step 1: Run the full test suite

Run: `cd /Users/pboueri/repos/intentc/src && uv run pytest -v`
Expected: Note which tests fail.

### Step 2: Fix any failures

Common fixes needed:
- Parser tests that check exact error message wording ("directory name" -> "directory path")
- Editor tests that reference flat target names (`"core"`, `"parser"`) -> update to path-based (`"core/intent"`, `"core/parser"`)
- Builder tests that create flat intent fixtures -> update to nested

### Step 3: Run full suite again

Run: `cd /Users/pboueri/repos/intentc/src && uv run pytest -v`
Expected: All PASS

### Step 4: Commit

```bash
git add -A
git commit -m "fix: update tests for module hierarchy changes"
```

---

## Task 9: Final Integration Verification

### Step 1: Validate all specs parse correctly

Run: `cd /Users/pboueri/repos/intentc && uv run python -c "
from parser.parser import validate_all_specs
violations = validate_all_specs('.')
for v in violations:
    print(f'{v.severity}: {v.message}')
if not violations:
    print('All specs valid')
"`
Expected: "All specs valid" or only warnings.

### Step 2: Verify the editor starts

Run: `cd /Users/pboueri/repos/intentc && uv run intentc edit . --port 9999 &`
Then: Open http://localhost:9999 and verify the tree view renders with modules, features are clickable, and upstream deps show.
Kill: `kill %1`

### Step 3: Run full test suite one more time

Run: `cd /Users/pboueri/repos/intentc/src && uv run pytest -v`
Expected: All PASS

### Step 4: Final commit

```bash
git add -A
git commit -m "feat: complete module hierarchy with tree view and upstream deps"
```
