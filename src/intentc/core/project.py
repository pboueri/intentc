from __future__ import annotations

import fnmatch
from collections import deque
from pathlib import Path
from typing import Any

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


class FeatureNode(BaseModel):
    path: str
    intents: list[IntentFile] = Field(default_factory=list)
    validations: list[ValidationFile] = Field(default_factory=list)

    @property
    def depends_on(self) -> list[str]:
        seen: set[str] = set()
        deps: list[str] = []
        for intent in self.intents:
            for dep in intent.depends_on:
                if dep not in seen:
                    seen.add(dep)
                    deps.append(dep)
        return deps


class Project(BaseModel):
    project_intent: ProjectIntent
    implementations: dict[str, Implementation] = Field(default_factory=dict)
    assertions: list[ValidationFile] = Field(default_factory=list)
    features: dict[str, FeatureNode] = Field(default_factory=dict)
    intent_dir: Path | None = None

    def resolve_implementation(self, name: str | None = None) -> Implementation | None:
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
            "and no 'default'. Specify one explicitly."
        )

    def _require_feature(self, feature_path: str) -> None:
        if feature_path not in self.features:
            raise KeyError(f"Feature '{feature_path}' not found. Available: {list(self.features.keys())}")

    def parents(self, feature_path: str) -> list[str]:
        self._require_feature(feature_path)
        return self.features[feature_path].depends_on

    def ancestors(self, feature_path: str) -> set[str]:
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
        self._require_feature(feature_path)
        return [
            fp for fp, node in self.features.items()
            if feature_path in node.depends_on
        ]

    def descendants(self, feature_path: str) -> set[str]:
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
        in_degree: dict[str, int] = {fp: 0 for fp in self.features}
        for fp, node in self.features.items():
            for dep in node.depends_on:
                if dep in in_degree:
                    in_degree[fp] += 1

        queue: deque[str] = deque(fp for fp, deg in in_degree.items() if deg == 0)
        order: list[str] = []

        while queue:
            fp = queue.popleft()
            order.append(fp)
            for child_fp, child_node in self.features.items():
                if fp in child_node.depends_on:
                    in_degree[child_fp] -= 1
                    if in_degree[child_fp] == 0:
                        queue.append(child_fp)

        if len(order) != len(self.features):
            missing = set(self.features.keys()) - set(order)
            raise ValueError(f"Cycle detected in feature dependencies involving: {missing}")

        return order


def _feature_path_from_dir(intent_dir: Path, feature_dir: Path) -> str:
    return str(feature_dir.relative_to(intent_dir))


def _is_feature_dir(d: Path) -> bool:
    """A directory is a feature if it contains at least one .ic or .icv file."""
    return any(d.glob("*.ic")) or any(d.glob("*.icv"))


def _expand_wildcards(
    features: dict[str, FeatureNode],
    errors: list[ParseError],
) -> None:
    all_paths = list(features.keys())
    for fp, node in features.items():
        for intent in node.intents:
            expanded: list[str] = []
            for dep in intent.depends_on:
                if any(c in dep for c in "*?["):
                    matches = [p for p in all_paths if fnmatch.fnmatch(p, dep)]
                    if not matches:
                        errors.append(ParseError(
                            path=intent.source_path or Path(fp),
                            field="depends_on",
                            message=f"Wildcard pattern '{dep}' matched no features",
                        ))
                    else:
                        expanded.extend(matches)
                else:
                    expanded.append(dep)
            # Deduplicate while preserving order
            seen: set[str] = set()
            deduped: list[str] = []
            for d in expanded:
                if d not in seen:
                    seen.add(d)
                    deduped.append(d)
            intent.depends_on = deduped


def load_project(intent_dir: Path) -> Project:
    intent_dir = Path(intent_dir)
    errors: list[ParseError] = []

    if not intent_dir.is_dir():
        raise ParseErrors([ParseError(
            path=intent_dir, field=None,
            message="Intent directory does not exist",
        )])

    # Parse project.ic
    project_ic = intent_dir / "project.ic"
    project_intent: ProjectIntent | None = None
    if not project_ic.exists():
        errors.append(ParseError(path=project_ic, field=None, message="Missing required project.ic"))
    else:
        try:
            project_intent = parse_intent_file(project_ic, as_project=True)  # type: ignore[assignment]
        except ParseErrors as e:
            errors.extend(e.errors)

    # Parse implementations
    implementations: dict[str, Implementation] = {}
    impl_dir = intent_dir / "implementations"
    if impl_dir.is_dir():
        for ic_file in sorted(impl_dir.glob("*.ic")):
            try:
                impl = parse_intent_file(ic_file, as_implementation=True)  # type: ignore[assignment]
                implementations[ic_file.stem] = impl
            except ParseErrors as e:
                errors.extend(e.errors)

    # Parse assertions
    assertions: list[ValidationFile] = []
    assertions_dir = intent_dir / "assertions"
    if assertions_dir.is_dir():
        for icv_file in sorted(assertions_dir.glob("*.icv")):
            try:
                assertions.append(parse_validation_file(icv_file))
            except ParseErrors as e:
                errors.extend(e.errors)

    # Discover and parse features
    features: dict[str, FeatureNode] = {}
    skip_dirs = {"implementations", "assertions"}

    for entry in sorted(intent_dir.iterdir()):
        if not entry.is_dir() or entry.name in skip_dirs or entry.name.startswith("."):
            continue
        _discover_features(intent_dir, entry, features, errors)

    # Expand wildcards
    _expand_wildcards(features, errors)

    if errors:
        raise ParseErrors(errors)

    return Project(
        project_intent=project_intent,  # type: ignore[arg-type]
        implementations=implementations,
        assertions=assertions,
        features=features,
        intent_dir=intent_dir,
    )


def _discover_features(
    intent_dir: Path,
    current_dir: Path,
    features: dict[str, FeatureNode],
    errors: list[ParseError],
) -> None:
    if _is_feature_dir(current_dir):
        feature_path = _feature_path_from_dir(intent_dir, current_dir)
        intents: list[IntentFile] = []
        validations: list[ValidationFile] = []

        for ic_file in sorted(current_dir.glob("*.ic")):
            try:
                intents.append(parse_intent_file(ic_file))  # type: ignore[arg-type]
            except ParseErrors as e:
                errors.extend(e.errors)

        for icv_file in sorted(current_dir.glob("*.icv")):
            try:
                validations.append(parse_validation_file(icv_file))
            except ParseErrors as e:
                errors.extend(e.errors)

        features[feature_path] = FeatureNode(
            path=feature_path,
            intents=intents,
            validations=validations,
        )

    # Recurse into subdirectories
    for entry in sorted(current_dir.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            _discover_features(intent_dir, entry, features, errors)


def write_project(project: Project, dest_dir: Path) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Write project.ic
    write_intent_file(project.project_intent, dest_dir / "project.ic")

    # Write implementations
    for name, impl in project.implementations.items():
        write_intent_file(impl, dest_dir / "implementations" / f"{name}.ic")

    # Write assertions
    for assertion in project.assertions:
        if assertion.source_path and project.intent_dir:
            rel = assertion.source_path.relative_to(project.intent_dir)
            write_validation_file(assertion, dest_dir / rel)
        else:
            write_validation_file(assertion, dest_dir / "assertions" / f"{assertion.target}.icv")

    # Write features
    for fp, node in project.features.items():
        feature_dir = dest_dir / fp
        for intent in node.intents:
            if intent.source_path and project.intent_dir:
                rel = intent.source_path.relative_to(project.intent_dir)
                write_intent_file(intent, dest_dir / rel)
            else:
                write_intent_file(intent, feature_dir / f"{intent.name}.ic")

        for vf in node.validations:
            if vf.source_path and project.intent_dir:
                rel = vf.source_path.relative_to(project.intent_dir)
                write_validation_file(vf, dest_dir / rel)
            else:
                write_validation_file(vf, feature_dir / "validations.icv")

    return dest_dir


def blank_project(name: str) -> Project:
    project_intent = ProjectIntent(name=name, body=f"# {name}\n\nDescribe your project here.")
    implementation = Implementation(
        name="default",
        body="# Default Implementation\n\nDefine your implementation details here.",
    )
    starter_intent = IntentFile(
        name="starter",
        body="# Starter Feature\n\nDescribe your first feature here.",
    )
    starter_node = FeatureNode(
        path="starter",
        intents=[starter_intent],
    )
    return Project(
        project_intent=project_intent,
        implementations={"default": implementation},
        features={"starter": starter_node},
    )
