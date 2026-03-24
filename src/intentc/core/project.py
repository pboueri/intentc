"""Project structure: DAG of features, loading, writing, and blank projects."""

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
    """A feature in the project DAG."""

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
    """The full intentc project loaded into memory."""

    project_intent: ProjectIntent
    implementations: dict[str, Implementation] = Field(default_factory=dict)
    assertions: list[ValidationFile] = Field(default_factory=list)
    features: dict[str, FeatureNode] = Field(default_factory=dict)
    intent_dir: Path | None = None

    def resolve_implementation(self, name: str | None = None) -> Implementation | None:
        """Resolve which implementation to use.

        If name is given, look it up. If null, use the single one or 'default'.
        Raises KeyError if name not found, ValueError if ambiguous.
        """
        if not self.implementations:
            return None
        if name is not None:
            if name not in self.implementations:
                raise KeyError(
                    f"Implementation '{name}' not found. "
                    f"Available: {', '.join(sorted(self.implementations))}"
                )
            return self.implementations[name]
        if len(self.implementations) == 1:
            return next(iter(self.implementations.values()))
        if "default" in self.implementations:
            return self.implementations["default"]
        raise ValueError(
            f"Ambiguous: multiple implementations available "
            f"({', '.join(sorted(self.implementations))}) and no 'default'. "
            f"Specify which one to use."
        )

    def _require_feature(self, feature_path: str) -> None:
        """Raise KeyError if feature_path not in features."""
        if feature_path not in self.features:
            raise KeyError(
                f"Feature '{feature_path}' not found. "
                f"Available: {', '.join(sorted(self.features)) or '(none)'}"
            )

    def parents(self, feature_path: str) -> list[str]:
        """Direct dependencies of a feature."""
        self._require_feature(feature_path)
        return self.features[feature_path].depends_on

    def ancestors(self, feature_path: str) -> set[str]:
        """All transitive dependencies (BFS)."""
        self._require_feature(feature_path)
        result: set[str] = set()
        queue: deque[str] = deque(self.features[feature_path].depends_on)
        while queue:
            dep = queue.popleft()
            if dep in result:
                continue
            result.add(dep)
            if dep in self.features:
                queue.extend(self.features[dep].depends_on)
        return result

    def children(self, feature_path: str) -> list[str]:
        """Features that directly depend on this feature."""
        self._require_feature(feature_path)
        return [
            fp for fp, node in self.features.items()
            if feature_path in node.depends_on
        ]

    def descendants(self, feature_path: str) -> set[str]:
        """All features that transitively depend on this feature (BFS)."""
        self._require_feature(feature_path)
        result: set[str] = set()
        queue: deque[str] = deque(self.children(feature_path))
        while queue:
            fp = queue.popleft()
            if fp in result:
                continue
            result.add(fp)
            if fp in self.features:
                queue.extend(self.children(fp))
        return result

    def topological_order(self) -> list[str]:
        """Return feature paths in dependency-first topological order.

        Raises ValueError on cycle.
        """
        # Kahn's algorithm
        in_degree: dict[str, int] = {fp: 0 for fp in self.features}
        for fp, node in self.features.items():
            for dep in node.depends_on:
                if dep in in_degree:
                    in_degree[fp] += 1

        queue: deque[str] = deque(
            fp for fp, deg in in_degree.items() if deg == 0
        )
        result: list[str] = []
        while queue:
            fp = queue.popleft()
            result.append(fp)
            for child in self.children(fp):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(result) != len(self.features):
            missing = set(self.features) - set(result)
            raise ValueError(
                f"Dependency cycle detected involving: {', '.join(sorted(missing))}"
            )
        return result


def load_project(intent_dir: Path) -> Project:
    """Load the full project from an intent/ directory. Raises ParseErrors on failure."""
    intent_dir = Path(intent_dir)
    errors: list[ParseError] = []

    # Parse project.ic
    project_ic = intent_dir / "project.ic"
    if not project_ic.exists():
        errors.append(ParseError(project_ic, "project.ic not found in intent directory"))
        raise ParseErrors(errors)

    try:
        project_intent = parse_intent_file(project_ic, as_project=True)
    except ParseErrors as exc:
        errors.extend(exc.errors)
        raise ParseErrors(errors) from exc

    # Parse implementations
    implementations: dict[str, Implementation] = {}
    impl_dir = intent_dir / "implementations"
    if impl_dir.is_dir():
        for ic_file in sorted(impl_dir.glob("*.ic")):
            try:
                impl = parse_intent_file(ic_file, as_implementation=True)
                assert isinstance(impl, Implementation)
                implementations[impl.name] = impl
            except ParseErrors as exc:
                errors.extend(exc.errors)

    # Parse assertions
    assertions: list[ValidationFile] = []
    assert_dir = intent_dir / "assertions"
    if assert_dir.is_dir():
        for icv_file in sorted(assert_dir.glob("*.icv")):
            try:
                vf = parse_validation_file(icv_file)
                assertions.append(vf)
            except ParseErrors as exc:
                errors.extend(exc.errors)

    # Discover features: any directory under intent_dir that contains .ic files,
    # excluding top-level special dirs and files
    features: dict[str, FeatureNode] = {}
    skip_dirs = {"implementations", "assertions"}

    for ic_file in sorted(intent_dir.rglob("*.ic")):
        rel = ic_file.relative_to(intent_dir)
        # Skip top-level project.ic and files in special dirs
        if len(rel.parts) < 2:
            continue
        if rel.parts[0] in skip_dirs:
            continue

        # Feature path is the directory relative to intent_dir
        feature_path = str(rel.parent).replace("\\", "/")

        if feature_path not in features:
            features[feature_path] = FeatureNode(path=feature_path)

        try:
            intent = parse_intent_file(ic_file)
            assert isinstance(intent, IntentFile)
            features[feature_path].intents.append(intent)
        except ParseErrors as exc:
            errors.extend(exc.errors)

    # Discover validation files for features
    for icv_file in sorted(intent_dir.rglob("*.icv")):
        rel = icv_file.relative_to(intent_dir)
        if len(rel.parts) < 2:
            continue
        if rel.parts[0] in skip_dirs:
            continue

        feature_path = str(rel.parent).replace("\\", "/")

        if feature_path not in features:
            # Validation file in a dir with no .ic files — still a valid feature dir
            # but we don't create a feature node for it since it has no intents
            continue

        try:
            vf = parse_validation_file(icv_file)
            features[feature_path].validations.append(vf)
        except ParseErrors as exc:
            errors.extend(exc.errors)

    # Wildcard dependency expansion
    all_feature_paths = set(features.keys())
    for node in features.values():
        for intent in node.intents:
            expanded: list[str] = []
            for dep in intent.depends_on:
                if "*" in dep or "?" in dep:
                    matches = sorted(
                        fp for fp in all_feature_paths
                        if fnmatch.fnmatch(fp, dep)
                    )
                    if not matches:
                        errors.append(
                            ParseError(
                                intent.source_path or Path("<unknown>"),
                                f"wildcard dependency '{dep}' matched no features",
                                field="depends_on",
                            )
                        )
                    expanded.extend(matches)
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

    # Write project.ic
    write_intent_file(project.project_intent, dest_dir / "project.ic")

    # Write implementations
    for impl in project.implementations.values():
        impl_path = dest_dir / "implementations" / f"{impl.name}.ic"
        write_intent_file(impl, impl_path)

    # Write assertions
    for vf in project.assertions:
        if vf.source_path:
            # Preserve original filename
            assert_path = dest_dir / "assertions" / vf.source_path.name
        else:
            assert_path = dest_dir / "assertions" / "assertion.icv"
        write_validation_file(vf, assert_path)

    # Write features
    for feature_path, node in project.features.items():
        feature_dir = dest_dir / feature_path
        for intent in node.intents:
            if intent.source_path:
                ic_path = feature_dir / intent.source_path.name
            else:
                ic_path = feature_dir / f"{intent.name}.ic"
            write_intent_file(intent, ic_path)

        for vf in node.validations:
            if vf.source_path:
                icv_path = feature_dir / vf.source_path.name
            else:
                icv_path = feature_dir / "validations.icv"
            write_validation_file(vf, icv_path)

        # Copy supporting files referenced by intents
        for intent in node.intents:
            if not intent.source_path:
                continue
            src_dir = intent.source_path.parent
            for ref in intent.file_references:
                src_file = (src_dir / ref).resolve()
                if src_file.exists() and src_file.is_file():
                    dest_file = feature_dir / ref
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    if not dest_file.exists():
                        shutil.copy2(src_file, dest_file)

    return dest_dir


def blank_project(name: str) -> Project:
    """Create a minimal starter project with project.ic and one starter feature."""
    project_intent = ProjectIntent(
        name=name,
        body=f"# {name}\n\nDescribe your project here.",
    )
    impl = Implementation(
        name="default",
        body="# Default Implementation\n\nDescribe your implementation choices here.",
    )
    starter_intent = IntentFile(
        name="starter",
        depends_on=[],
        body="# Starter Feature\n\nDescribe your first feature here.",
    )
    starter_node = FeatureNode(
        path="starter",
        intents=[starter_intent],
    )
    return Project(
        project_intent=project_intent,
        implementations={"default": impl},
        features={"starter": starter_node},
    )
