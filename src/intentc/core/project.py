"""Project structure: loading an intent/ directory into a `Project` DAG, writing it
back out, creating a blank starter project, and linting an already-loaded project.
"""

from __future__ import annotations

import re
import fnmatch
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from intentc.core.models import (
    Implementation,
    IntentFile,
    ParseError,
    ParseErrors,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
    ValidationType,
)
from intentc.core.parser import (
    parse_intent_file,
    parse_validation_file,
    write_intent_file,
    write_validation_file,
)

_EXCLUDED_TOP_DIRS = {"implementations", "assertions"}
_WILDCARD_CHARS = ("*", "?", "[")


def _is_hidden(name: str) -> bool:
    return name.startswith(".") or name.startswith("_")


def _is_wildcard(dep: str) -> bool:
    return any(ch in dep for ch in _WILDCARD_CHARS)


def _iter_candidate_dirs(intent_dir: Path):
    """Walk intent_dir, yielding (dir_path, rel_posix, ic_files, icv_files) for every
    directory except intent_dir itself and the top-level implementations/ and assertions/.
    """
    for dirpath, dirnames, _filenames in os.walk(intent_dir):
        current = Path(dirpath)
        dirnames.sort()
        if current == intent_dir:
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_TOP_DIRS and not _is_hidden(d)]
            continue
        dirnames[:] = [d for d in dirnames if not _is_hidden(d)]
        rel = current.relative_to(intent_dir).as_posix()
        ic_files = sorted(
            p for p in current.iterdir() if p.is_file() and p.suffix == ".ic" and not _is_hidden(p.name)
        )
        icv_files = sorted(
            p for p in current.iterdir() if p.is_file() and p.suffix == ".icv" and not _is_hidden(p.name)
        )
        yield current, rel, ic_files, icv_files


def _topological_order(features: dict[str, "FeatureNode"]) -> list[str]:
    """Dependency-first topological order. Raises ValueError on a cycle."""
    order: list[str] = []
    perm_mark: set[str] = set()
    temp_mark: set[str] = set()
    path_stack: list[str] = []

    def visit(node: str) -> None:
        if node in perm_mark:
            return
        if node in temp_mark:
            idx = path_stack.index(node)
            cycle = path_stack[idx:] + [node]
            raise ValueError(f"dependency cycle detected: {' -> '.join(cycle)}")
        temp_mark.add(node)
        path_stack.append(node)
        for dep in features[node].depends_on:
            if dep in features:
                visit(dep)
        path_stack.pop()
        temp_mark.discard(node)
        perm_mark.add(node)
        order.append(node)

    for feature_path in features:
        visit(feature_path)
    return order


class FeatureNode(BaseModel):
    """A single feature directory under intent/: its intents and validations."""

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
    """A fully loaded intentc project: the project intent, implementations, project-level
    assertions, and the DAG of features."""

    project_intent: ProjectIntent
    implementations: dict[str, Implementation] = Field(default_factory=dict)
    assertions: list[ValidationFile] = Field(default_factory=list)
    features: dict[str, FeatureNode] = Field(default_factory=dict)
    intent_dir: Optional[Path] = None

    def resolve_implementation(self, name: Optional[str] = None) -> Optional[Implementation]:
        """Resolve which implementation to use.

        If name is given, look it up (KeyError if missing). If name is null, use the
        single implementation if there is exactly one, or 'default' if there are several
        and one of them is named 'default'. Raises ValueError if there are several and
        none is named 'default'. Returns None if there are no implementations at all.
        """
        if name is not None:
            if name not in self.implementations:
                raise KeyError(name)
            return self.implementations[name]
        if not self.implementations:
            return None
        if len(self.implementations) == 1:
            return next(iter(self.implementations.values()))
        if "default" in self.implementations:
            return self.implementations["default"]
        available = ", ".join(sorted(self.implementations))
        raise ValueError(f"multiple implementations found ({available}); specify one by name")

    def _require_feature(self, feature_path: str) -> None:
        if feature_path not in self.features:
            raise KeyError(feature_path)

    def parents(self, feature_path: str) -> list[str]:
        """Direct dependencies of a feature."""
        self._require_feature(feature_path)
        return list(self.features[feature_path].depends_on)

    def ancestors(self, feature_path: str) -> set[str]:
        """All transitive dependencies (BFS)."""
        self._require_feature(feature_path)
        seen: set[str] = set()
        queue = list(self.features[feature_path].depends_on)
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            if current in self.features:
                queue.extend(self.features[current].depends_on)
        return seen

    def children(self, feature_path: str) -> list[str]:
        """Features that directly depend on this feature."""
        self._require_feature(feature_path)
        return [path for path, node in self.features.items() if feature_path in node.depends_on]

    def descendants(self, feature_path: str) -> set[str]:
        """All features that transitively depend on this feature (BFS)."""
        self._require_feature(feature_path)
        seen: set[str] = set()
        queue = self.children(feature_path)
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            queue.extend(self.children(current))
        return seen

    def topological_order(self) -> list[str]:
        """Return feature paths in dependency-first topological order. Raises ValueError
        on a cycle."""
        return _topological_order(self.features)

    def buildable_after(self, built: set[str]) -> list[str]:
        """Features not yet in `built` whose dependencies are all in `built`."""
        return [
            path
            for path, node in self.features.items()
            if path not in built and all(dep in built for dep in node.depends_on)
        ]


@dataclass
class ProjectIssue:
    """A single lint finding from `check_project`."""

    level: str
    path: Optional[Path]
    feature: str
    message: str

    def __str__(self) -> str:
        if self.path is None:
            return f"{self.level}: {self.message}"
        return f"{self.level}: {self.path}: {self.message}"


def load_project(intent_dir: Path) -> Project:
    """Load the full project from an intent/ directory. Raises ParseErrors on failure."""
    intent_dir = Path(intent_dir)
    errors: list[ParseError] = []

    project_intent: Optional[ProjectIntent] = None
    project_ic = intent_dir / "project.ic"
    if not project_ic.is_file():
        errors.append(ParseError(project_ic, None, "project.ic not found; every project requires one"))
    else:
        try:
            parsed = parse_intent_file(project_ic, as_project=True)
            assert isinstance(parsed, ProjectIntent)
            project_intent = parsed
        except ParseErrors as exc:
            errors.extend(exc.errors)

    implementations: dict[str, Implementation] = {}
    impl_dir = intent_dir / "implementations"
    if impl_dir.is_dir():
        for path in sorted(impl_dir.glob("*.ic")):
            if _is_hidden(path.name):
                continue
            try:
                impl = parse_intent_file(path, as_implementation=True)
                assert isinstance(impl, Implementation)
                implementations[impl.name] = impl
            except ParseErrors as exc:
                errors.extend(exc.errors)

    assertions: list[ValidationFile] = []
    assertions_dir = intent_dir / "assertions"
    if assertions_dir.is_dir():
        for path in sorted(assertions_dir.glob("*.icv")):
            if _is_hidden(path.name):
                continue
            try:
                assertions.append(parse_validation_file(path))
            except ParseErrors as exc:
                errors.extend(exc.errors)

    features: dict[str, FeatureNode] = {}
    for _dir_path, rel, ic_files, icv_files in sorted(
        _iter_candidate_dirs(intent_dir), key=lambda item: item[1]
    ):
        if not ic_files:
            continue
        intents: list[IntentFile] = []
        for path in ic_files:
            try:
                intent = parse_intent_file(path)
                assert isinstance(intent, IntentFile)
                intents.append(intent)
            except ParseErrors as exc:
                errors.extend(exc.errors)
        validations: list[ValidationFile] = []
        for path in icv_files:
            try:
                validations.append(parse_validation_file(path))
            except ParseErrors as exc:
                errors.extend(exc.errors)
        features[rel] = FeatureNode(path=rel, intents=intents, validations=validations)

    feature_paths = set(features.keys())

    for feature in features.values():
        for intent in feature.intents:
            expanded: list[str] = []
            seen: set[str] = set()
            for dep in intent.depends_on:
                if _is_wildcard(dep):
                    matches = sorted(m for m in fnmatch.filter(feature_paths, dep) if m != feature.path)
                    if not matches:
                        errors.append(
                            ParseError(
                                intent.source_path, "depends_on", f"wildcard '{dep}' matches no features"
                            )
                        )
                        continue
                    for match in matches:
                        if match not in seen:
                            seen.add(match)
                            expanded.append(match)
                else:
                    if dep not in seen:
                        seen.add(dep)
                        expanded.append(dep)
            intent.depends_on[:] = expanded

    for feature in features.values():
        for intent in feature.intents:
            for dep in intent.depends_on:
                if dep == feature.path:
                    errors.append(
                        ParseError(
                            intent.source_path, "depends_on", f"feature '{feature.path}' cannot depend on itself"
                        )
                    )
                elif dep not in feature_paths:
                    available = ", ".join(sorted(feature_paths))
                    errors.append(
                        ParseError(
                            intent.source_path,
                            "depends_on",
                            f"unknown dependency '{dep}' (available: {available})",
                        )
                    )

    if not errors:
        try:
            _topological_order(features)
        except ValueError as exc:
            errors.append(ParseError(intent_dir, None, str(exc)))

    if errors:
        raise ParseErrors(errors)

    assert project_intent is not None
    return Project(
        project_intent=project_intent,
        implementations=implementations,
        assertions=assertions,
        features=features,
        intent_dir=intent_dir,
    )


_LAYOUT_PREFIXES = {"intent", ".intentc"}


def _copy_file_references(intent: IntentFile | ProjectIntent | Implementation, dest_dir: Path) -> None:
    if intent.source_path is None:
        return
    src_base = intent.source_path.parent
    for ref in intent.file_references:
        if _is_wildcard(ref):
            continue
        src_path = src_base / ref
        if not src_path.is_file():
            continue
        dest_path = dest_dir / ref
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest_path)


def write_project(project: Project, dest_dir: Path) -> Path:
    """Write a project to a new directory. Returns the dest_dir path."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    write_intent_file(project.project_intent, dest_dir / "project.ic")
    _copy_file_references(project.project_intent, dest_dir)

    if project.implementations:
        impl_dir = dest_dir / "implementations"
        for impl in project.implementations.values():
            filename = impl.source_path.name if impl.source_path is not None else f"{impl.name}.ic"
            dest_path = impl_dir / filename
            write_intent_file(impl, dest_path)
            _copy_file_references(impl, dest_path.parent)

    if project.assertions:
        assertions_dir = dest_dir / "assertions"
        for idx, vf in enumerate(project.assertions):
            if vf.source_path is not None:
                filename = vf.source_path.name
            else:
                filename = f"{vf.target}.icv" if vf.target else f"assertion_{idx}.icv"
            write_validation_file(vf, assertions_dir / filename)

    for feature_path, node in project.features.items():
        feature_dir = dest_dir / feature_path
        leaf = feature_path.rsplit("/", 1)[-1]
        for intent in node.intents:
            filename = intent.source_path.name if intent.source_path is not None else f"{leaf}.ic"
            dest_path = feature_dir / filename
            write_intent_file(intent, dest_path)
            _copy_file_references(intent, dest_path.parent)
        for idx, vf in enumerate(node.validations):
            if vf.source_path is not None:
                filename = vf.source_path.name
            elif idx == 0:
                filename = "validation.icv"
            else:
                filename = f"validation_{idx}.icv"
            write_validation_file(vf, feature_dir / filename)

    return dest_dir


def blank_project(name: str) -> Project:
    """Create a minimal starter project: project.ic, a default implementation, and one
    starter feature with a worked-example validation."""
    project_intent = ProjectIntent(
        name=name,
        body=(
            f"# {name}\n\n"
            "Describe what this project is and why it exists.\n\n"
            "Run `intentc check` to lint the project structure, and `intentc build` "
            "to generate code from the intent files below.\n"
        ),
    )

    default_impl = Implementation(
        name="default",
        body=(
            "# Default Implementation\n\n"
            "Describe the language, libraries, and conventions the generated code "
            "should follow. This guides every feature build unless overridden with "
            "`--implementation`.\n"
        ),
    )

    starter_intent = IntentFile(
        name="starter",
        depends_on=[],
        body=(
            "# Starter Feature\n\n"
            "This is a worked example. Replace this body with what you actually want "
            "built, then run `intentc check` to lint the project and `intentc build` "
            "to generate it.\n"
        ),
    )

    starter_validation = ValidationFile(
        target="starter",
        version=1,
        validations=[
            Validation(
                name="starter-file-exists",
                type=ValidationType.FILE_EXISTS.value,
                severity=Severity.ERROR,
                args={"paths": ["README.md"]},
            )
        ],
    )

    starter_node = FeatureNode(
        path="starter",
        intents=[starter_intent],
        validations=[starter_validation],
    )

    return Project(
        project_intent=project_intent,
        implementations={"default": default_impl},
        assertions=[],
        features={"starter": starter_node},
        intent_dir=None,
    )


def _sort_key(issue: ProjectIssue) -> tuple[str, str]:
    return (str(issue.path) if issue.path is not None else "", issue.level)


def check_project(project: Project) -> list[ProjectIssue]:
    """Deterministic lint of an already-loaded project. Never calls an agent."""
    issues: list[ProjectIssue] = []

    if project.intent_dir is not None:
        for _dir_path, rel, ic_files, icv_files in _iter_candidate_dirs(project.intent_dir):
            if icv_files and not ic_files:
                for icv_path in icv_files:
                    issues.append(
                        ProjectIssue(
                            level="error",
                            path=icv_path,
                            feature=rel,
                            message=(
                                f"'{icv_path.name}' has no accompanying .ic file in '{rel}'; "
                                "add an intent file or remove this validation file"
                            ),
                        )
                    )

    def check_validation_file(vf: ValidationFile, feature_path: str, is_feature: bool) -> None:
        target = vf.target
        if not target or target == "project":
            return
        if is_feature:
            if target != feature_path:
                issues.append(
                    ProjectIssue(
                        level="warning",
                        path=vf.source_path,
                        feature=feature_path,
                        message=f"target '{target}' differs from the directory it lives in ('{feature_path}'); rename to `target: {feature_path}`",
                    )
                )
        elif target not in project.features:
            issues.append(
                ProjectIssue(
                    level="error",
                    path=vf.source_path,
                    feature=feature_path,
                    message=f"target '{target}' does not name an existing feature or 'project'",
                )
            )

    for vf in project.assertions:
        check_validation_file(vf, "", is_feature=False)

    for feature_path, node in project.features.items():
        leaf = feature_path.rsplit("/", 1)[-1]

        for vf in node.validations:
            check_validation_file(vf, feature_path, is_feature=True)
            for validation in vf.validations:
                if validation.type == ValidationType.AGENT_VALIDATION.value:
                    rubric = validation.args.get("rubric", "")
                    if len(rubric) < 40:
                        issues.append(
                            ProjectIssue(
                                level="warning",
                                path=vf.source_path,
                                feature=feature_path,
                                message=(
                                    f"agent_validation '{validation.name}' has a rubric shorter than "
                                    "40 characters; add more detail so it can be judged"
                                ),
                            )
                        )

        for intent in node.intents:
            if intent.name != leaf:
                issues.append(
                    ProjectIssue(
                        level="warning",
                        path=intent.source_path,
                        feature=feature_path,
                        message=(
                            f"intent name '{intent.name}' differs from its directory '{feature_path}'; "
                            f"rename to `name: {leaf}`"
                        ),
                    )
                )
            if intent.source_path is not None:
                base = intent.source_path.parent
                for ref in intent.file_references:
                    if _is_wildcard(ref):
                        continue
                    # Intents routinely describe the project layout (intent/..., .intentc/...);
                    # those are descriptions, not supporting files.
                    if re.sub(r"^(\.\.?/)+", "", ref).split("/", 1)[0] in _LAYOUT_PREFIXES:
                        continue
                    if not (base / ref).exists():
                        issues.append(
                            ProjectIssue(
                                level="warning",
                                path=intent.source_path,
                                feature=feature_path,
                                message=f"referenced file '{ref}' does not exist relative to this intent file",
                            )
                        )

        if not any(intent.body.strip() for intent in node.intents):
            issues.append(
                ProjectIssue(
                    level="warning",
                    path=None,
                    feature=feature_path,
                    message=f"feature '{feature_path}' has an empty body; there is nothing for the agent to build",
                )
            )

        total_validations = sum(len(vf.validations) for vf in node.validations)
        if total_validations == 0:
            issues.append(
                ProjectIssue(
                    level="warning",
                    path=None,
                    feature=feature_path,
                    message=f"feature '{feature_path}' has no validations; add a validations.icv next to it",
                )
            )

    if not project.implementations:
        impl_dir = project.intent_dir / "implementations" if project.intent_dir is not None else None
        issues.append(
            ProjectIssue(
                level="warning",
                path=impl_dir,
                feature="",
                message="implementations/ is empty or missing; builds will have no implementation guidance",
            )
        )

    issues.sort(key=_sort_key)
    return issues
