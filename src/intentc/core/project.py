"""Project structure: loading, writing, and DAG traversal for intentc projects."""

from __future__ import annotations

import fnmatch
import shutil
from collections import deque
from pathlib import Path

from pydantic import BaseModel, Field

from intentc.core.models import (
    Implementation,
    IntentFile,
    ParseError,
    ParseErrors,
    ProjectIntent,
    ValidationFile,
)
from intentc.core.parser import (
    parse_intent_file,
    parse_validation_file,
    write_intent_file,
    write_validation_file,
)


class FeatureNode(BaseModel):
    """A single feature within the project DAG."""

    path: str
    intents: list[IntentFile] = Field(default_factory=list)
    validations: list[ValidationFile] = Field(default_factory=list)

    @property
    def depends_on(self) -> list[str]:
        """Combined dependencies from all intent files, deduplicated, order-preserving."""
        seen: set[str] = set()
        result: list[str] = []
        for intent in self.intents:
            for dep in intent.depends_on:
                if dep not in seen:
                    seen.add(dep)
                    result.append(dep)
        return result


class Project(BaseModel):
    """The full intentc project, parsed from an intent/ directory."""

    project_intent: ProjectIntent
    implementations: dict[str, Implementation] = Field(default_factory=dict)
    assertions: list[ValidationFile] = Field(default_factory=list)
    features: dict[str, FeatureNode] = Field(default_factory=dict)
    intent_dir: Path | None = None

    def resolve_implementation(self, name: str | None = None) -> Implementation | None:
        """Resolve which implementation to use.

        If name is given, look it up. If None, use the single one or 'default'.
        Raises KeyError if name not found, ValueError if ambiguous.
        """
        if not self.implementations:
            return None
        if name is not None:
            if name not in self.implementations:
                raise KeyError(f"Implementation '{name}' not found. Available: {list(self.implementations.keys())}")
            return self.implementations[name]
        if len(self.implementations) == 1:
            return next(iter(self.implementations.values()))
        if "default" in self.implementations:
            return self.implementations["default"]
        raise ValueError(
            f"Ambiguous: multiple implementations found ({list(self.implementations.keys())}) "
            "and no 'default'. Pass an explicit name."
        )

    def _require_feature(self, feature_path: str) -> None:
        """Raise KeyError if feature_path not in features."""
        if feature_path not in self.features:
            raise KeyError(f"Feature '{feature_path}' not found. Available: {sorted(self.features.keys())}")

    def parents(self, feature_path: str) -> list[str]:
        """Direct dependencies of a feature."""
        self._require_feature(feature_path)
        return self.features[feature_path].depends_on

    def ancestors(self, feature_path: str) -> set[str]:
        """All transitive dependencies (BFS)."""
        self._require_feature(feature_path)
        visited: set[str] = set()
        queue: deque[str] = deque(self.features[feature_path].depends_on)
        while queue:
            dep = queue.popleft()
            if dep in visited:
                continue
            visited.add(dep)
            if dep in self.features:
                queue.extend(self.features[dep].depends_on)
        return visited

    def children(self, feature_path: str) -> list[str]:
        """Features that directly depend on this feature."""
        self._require_feature(feature_path)
        result: list[str] = []
        for fp, node in self.features.items():
            if feature_path in node.depends_on:
                result.append(fp)
        return result

    def descendants(self, feature_path: str) -> set[str]:
        """All features that transitively depend on this feature (BFS)."""
        self._require_feature(feature_path)
        visited: set[str] = set()
        queue: deque[str] = deque(self.children(feature_path))
        while queue:
            fp = queue.popleft()
            if fp in visited:
                continue
            visited.add(fp)
            for child_fp, child_node in self.features.items():
                if fp in child_node.depends_on and child_fp not in visited:
                    queue.append(child_fp)
        return visited

    def topological_order(self) -> list[str]:
        """Return feature paths in dependency-first topological order. Raises ValueError on cycle."""
        in_degree: dict[str, int] = {fp: 0 for fp in self.features}
        for fp, node in self.features.items():
            for dep in node.depends_on:
                if dep in self.features:
                    in_degree[fp] += 1

        queue: deque[str] = deque(fp for fp, deg in in_degree.items() if deg == 0)
        order: list[str] = []

        while queue:
            fp = queue.popleft()
            order.append(fp)
            for other_fp in self.features:
                if fp in self.features[other_fp].depends_on:
                    in_degree[other_fp] -= 1
                    if in_degree[other_fp] == 0:
                        queue.append(other_fp)

        if len(order) != len(self.features):
            remaining = set(self.features.keys()) - set(order)
            raise ValueError(f"Dependency cycle detected among features: {sorted(remaining)}")
        return order


def _derive_feature_path(intent_dir: Path, file_path: Path) -> str:
    """Derive a feature path from the file's location relative to intent_dir.

    E.g. intent/core/project/project.ic -> core/project
    """
    rel = file_path.relative_to(intent_dir)
    # The feature path is the parent directory path (excluding 'intent/' itself)
    return str(rel.parent)


def load_project(intent_dir: Path) -> Project:
    """Load the full project from an intent/ directory. Raises ParseErrors on failure."""
    intent_dir = Path(intent_dir)
    errors: list[ParseError] = []

    # --- project.ic ---
    project_ic = intent_dir / "project.ic"
    if not project_ic.exists():
        errors.append(ParseError(path=project_ic, message="project.ic not found in intent directory"))
        raise ParseErrors(errors)

    try:
        project_intent = parse_intent_file(project_ic, as_project=True)
    except ParseErrors as exc:
        errors.extend(exc.errors)
        raise ParseErrors(errors)

    # --- implementations/ ---
    implementations: dict[str, Implementation] = {}
    impl_dir = intent_dir / "implementations"
    if impl_dir.is_dir():
        for ic_file in sorted(impl_dir.glob("*.ic")):
            try:
                impl = parse_intent_file(ic_file, as_implementation=True)
                implementations[impl.name] = impl
            except ParseErrors as exc:
                errors.extend(exc.errors)

    # --- assertions/ ---
    assertions: list[ValidationFile] = []
    assertions_dir = intent_dir / "assertions"
    if assertions_dir.is_dir():
        for icv_file in sorted(assertions_dir.glob("*.icv")):
            try:
                vf = parse_validation_file(icv_file)
                assertions.append(vf)
            except ParseErrors as exc:
                errors.extend(exc.errors)

    # --- features (everything else) ---
    features: dict[str, FeatureNode] = {}

    # Collect all .ic and .icv files under intent/, excluding top-level files and
    # the implementations/ and assertions/ directories.
    skip_dirs = {intent_dir / "implementations", intent_dir / "assertions"}

    for ic_file in sorted(intent_dir.rglob("*.ic")):
        # Skip top-level project.ic
        if ic_file == project_ic:
            continue
        # Skip implementations dir
        if any(ic_file.is_relative_to(sd) for sd in skip_dirs):
            continue
        feature_path = _derive_feature_path(intent_dir, ic_file)
        if not feature_path or feature_path == ".":
            continue
        try:
            intent = parse_intent_file(ic_file)
            if feature_path not in features:
                features[feature_path] = FeatureNode(path=feature_path)
            features[feature_path].intents.append(intent)
        except ParseErrors as exc:
            errors.extend(exc.errors)

    for icv_file in sorted(intent_dir.rglob("*.icv")):
        if any(icv_file.is_relative_to(sd) for sd in skip_dirs):
            continue
        feature_path = _derive_feature_path(intent_dir, icv_file)
        if not feature_path or feature_path == ".":
            continue
        try:
            vf = parse_validation_file(icv_file)
            if feature_path not in features:
                features[feature_path] = FeatureNode(path=feature_path)
            features[feature_path].validations.append(vf)
        except ParseErrors as exc:
            errors.extend(exc.errors)

    # --- Wildcard dependency expansion ---
    all_feature_paths = list(features.keys())
    for node in features.values():
        for intent in node.intents:
            expanded: list[str] = []
            for dep in intent.depends_on:
                if any(c in dep for c in ("*", "?", "[")):
                    matched = [fp for fp in all_feature_paths if fnmatch.fnmatch(fp, dep)]
                    if not matched:
                        errors.append(ParseError(
                            path=intent.source_path or Path("<unknown>"),
                            field="depends_on",
                            message=f"Wildcard pattern '{dep}' matched no features",
                        ))
                    else:
                        expanded.extend(matched)
                else:
                    expanded.append(dep)
            intent.depends_on = expanded

    if errors:
        raise ParseErrors(errors)

    return Project(
        project_intent=project_intent,
        implementations=implementations,
        assertions=assertions,
        features=features,
        intent_dir=intent_dir,
    )


def write_project(project: Project, dest_dir: Path) -> Path:
    """Write a project to a new directory. Returns the dest_dir path."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # project.ic
    write_intent_file(project.project_intent, dest_dir / "project.ic")

    # implementations/
    if project.implementations:
        impl_dir = dest_dir / "implementations"
        impl_dir.mkdir(parents=True, exist_ok=True)
        for impl in project.implementations.values():
            name = impl.source_path.name if impl.source_path else f"{impl.name}.ic"
            write_intent_file(impl, impl_dir / name)

    # assertions/
    if project.assertions:
        assertions_dir = dest_dir / "assertions"
        assertions_dir.mkdir(parents=True, exist_ok=True)
        for vf in project.assertions:
            name = vf.source_path.name if vf.source_path else "assertion.icv"
            write_validation_file(vf, assertions_dir / name)

    # features
    for feature_path, node in project.features.items():
        feature_dir = dest_dir / feature_path
        feature_dir.mkdir(parents=True, exist_ok=True)
        for intent in node.intents:
            name = intent.source_path.name if intent.source_path else f"{intent.name}.ic"
            write_intent_file(intent, feature_dir / name)
        for vf in node.validations:
            name = vf.source_path.name if vf.source_path else "validation.icv"
            write_validation_file(vf, feature_dir / name)

    # Copy supporting files referenced in intents
    if project.intent_dir:
        _copy_supporting_files(project, dest_dir)

    return dest_dir


def _copy_supporting_files(project: Project, dest_dir: Path) -> None:
    """Copy non-.ic/.icv files from feature directories."""
    intent_dir = project.intent_dir
    if not intent_dir:
        return

    skip_dirs = {intent_dir / "implementations", intent_dir / "assertions"}

    for file_path in sorted(intent_dir.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix in (".ic", ".icv"):
            continue
        if file_path == intent_dir / "project.ic":
            continue
        if any(file_path.is_relative_to(sd) for sd in skip_dirs):
            continue

        rel = file_path.relative_to(intent_dir)
        dest_file = dest_dir / rel
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, dest_file)


def blank_project(name: str) -> Project:
    """Create a minimal starter project with project.ic and one starter feature."""
    project_intent = ProjectIntent(
        name=name,
        body=f"# {name}\n\nDescribe your project here.\n",
    )

    starter_intent = IntentFile(
        name="starter",
        body="# Starter Feature\n\nDescribe your first feature here.\n",
    )

    starter_validation = ValidationFile(
        target="starter",
        validations=[],
    )

    features = {
        "starter": FeatureNode(
            path="starter",
            intents=[starter_intent],
            validations=[starter_validation],
        ),
    }

    return Project(
        project_intent=project_intent,
        features=features,
    )
