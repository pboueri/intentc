"""Project structure: loading, writing, and DAG traversal."""

from __future__ import annotations

import fnmatch
from collections import deque
from pathlib import Path

from pydantic import BaseModel, Field

from intentc.core.parser import (
    parse_intent_file,
    parse_validation_file,
    write_intent_file,
    write_validation_file,
)
from intentc.core.types import (
    Implementation,
    IntentFile,
    ParseError,
    ParseErrors,
    ProjectIntent,
    ValidationFile,
)


# ---------------------------------------------------------------------------
# FeatureNode
# ---------------------------------------------------------------------------


class FeatureNode(BaseModel):
    model_config = {"extra": "ignore"}

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


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class Project(BaseModel):
    model_config = {"extra": "ignore"}

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
                raise KeyError(f"Implementation '{name}' not found. Available: {sorted(self.implementations)}")
            return self.implementations[name]
        if len(self.implementations) == 1:
            return next(iter(self.implementations.values()))
        if "default" in self.implementations:
            return self.implementations["default"]
        raise ValueError(
            f"Ambiguous: multiple implementations found ({sorted(self.implementations)}) "
            "and no 'default'. Pass an explicit name."
        )

    # -- DAG traversal -------------------------------------------------------

    def _require_feature(self, feature_path: str) -> None:
        if feature_path not in self.features:
            raise KeyError(f"Feature '{feature_path}' not found in project")

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
        return [fp for fp, node in self.features.items() if feature_path in node.depends_on]

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
            queue.extend(self.children(fp))
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
            for child_fp, child_node in self.features.items():
                if fp in child_node.depends_on and child_fp not in order:
                    in_degree[child_fp] -= 1
                    if in_degree[child_fp] == 0:
                        queue.append(child_fp)

        if len(order) != len(self.features):
            missing = set(self.features) - set(order)
            raise ValueError(f"Dependency cycle detected involving: {sorted(missing)}")
        return order


# ---------------------------------------------------------------------------
# load_project
# ---------------------------------------------------------------------------


def load_project(intent_dir: Path) -> Project:
    """Load the full project from an intent/ directory. Raises ParseErrors on failure."""
    intent_dir = Path(intent_dir)
    errors: list[ParseError] = []

    # -- project.ic ----------------------------------------------------------
    project_ic = intent_dir / "project.ic"
    if not project_ic.exists():
        errors.append(ParseError(path=project_ic, field=None, message="project.ic not found"))
        raise ParseErrors(errors)

    try:
        project_intent = parse_intent_file(project_ic, as_project=True)
    except ParseErrors as exc:
        errors.extend(exc.errors)
        raise ParseErrors(errors)

    # -- implementations/ ----------------------------------------------------
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

    # -- assertions/ ---------------------------------------------------------
    assertions: list[ValidationFile] = []
    assertions_dir = intent_dir / "assertions"
    if assertions_dir.is_dir():
        for icv_file in sorted(assertions_dir.glob("*.icv")):
            try:
                assertions.append(parse_validation_file(icv_file))
            except ParseErrors as exc:
                errors.extend(exc.errors)

    # -- features (recursive scan) -------------------------------------------
    features: dict[str, FeatureNode] = {}
    _reserved = {"implementations", "assertions"}

    for ic_file in sorted(intent_dir.rglob("*.ic")):
        rel = ic_file.relative_to(intent_dir)
        parts = rel.parts
        # Skip project.ic and implementations/*.ic
        if len(parts) < 2:
            continue
        if parts[0] in _reserved:
            continue
        feature_path = str(Path(*parts[:-1]))
        if feature_path not in features:
            features[feature_path] = FeatureNode(path=feature_path)
        try:
            intent = parse_intent_file(ic_file)
            features[feature_path].intents.append(intent)
        except ParseErrors as exc:
            errors.extend(exc.errors)

    for icv_file in sorted(intent_dir.rglob("*.icv")):
        rel = icv_file.relative_to(intent_dir)
        parts = rel.parts
        if parts[0] in _reserved:
            continue
        if len(parts) < 2:
            continue
        feature_path = str(Path(*parts[:-1]))
        if feature_path not in features:
            features[feature_path] = FeatureNode(path=feature_path)
        try:
            features[feature_path].validations.append(parse_validation_file(icv_file))
        except ParseErrors as exc:
            errors.extend(exc.errors)

    # -- wildcard dependency expansion ---------------------------------------
    all_feature_paths = sorted(features)
    for fp, node in features.items():
        for intent in node.intents:
            expanded: list[str] = []
            for dep in intent.depends_on:
                if any(c in dep for c in ("*", "?", "[")):
                    matches = [p for p in all_feature_paths if fnmatch.fnmatch(p, dep)]
                    if not matches:
                        errors.append(
                            ParseError(
                                path=intent.source_path or Path(fp),
                                field="depends_on",
                                message=f"Wildcard pattern '{dep}' matched no features",
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


# ---------------------------------------------------------------------------
# write_project
# ---------------------------------------------------------------------------


def write_project(project: Project, dest_dir: Path) -> Path:
    """Write a project to a new directory. Returns the dest_dir path."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # project.ic
    write_intent_file(project.project_intent, dest_dir / "project.ic")

    # implementations/
    for impl in project.implementations.values():
        write_intent_file(impl, dest_dir / "implementations" / f"{impl.name}.ic")

    # assertions/
    for vf in project.assertions:
        if vf.source_path is not None:
            # Preserve original filename
            fname = vf.source_path.name
        else:
            fname = f"{vf.target}.icv"
        write_validation_file(vf, dest_dir / "assertions" / fname)

    # features
    for fp, node in project.features.items():
        for intent in node.intents:
            if intent.source_path is not None:
                fname = intent.source_path.name
            else:
                fname = f"{intent.name}.ic"
            write_intent_file(intent, dest_dir / fp / fname)
        for vf in node.validations:
            if vf.source_path is not None:
                fname = vf.source_path.name
            else:
                fname = f"{vf.target}.icv"
            write_validation_file(vf, dest_dir / fp / fname)

    return dest_dir


# ---------------------------------------------------------------------------
# blank_project
# ---------------------------------------------------------------------------


def blank_project(name: str) -> Project:
    """Create a minimal starter project with project.ic and one starter feature."""
    project_intent = ProjectIntent(
        name=name,
        body=f"# {name}\n\nDescribe your project here.\n",
    )
    impl = Implementation(
        name="default",
        body="# Default Implementation\n\nDescribe your implementation approach here.\n",
    )
    starter_intent = IntentFile(
        name="starter",
        body="# Starter Feature\n\nDescribe your first feature here.\n",
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
