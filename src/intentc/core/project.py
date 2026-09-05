"""Project structure: the feature DAG, loading/writing projects, and project lint."""

from __future__ import annotations

import difflib
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

_SPECIAL_DIRS = {"implementations", "assertions"}
_MIN_RUBRIC_LENGTH = 40


class FeatureNode(BaseModel):
    """A feature directory: its intents and validations."""

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


class ProjectIssue(BaseModel):
    """A deterministic lint finding from ``check_project``."""

    level: str  # "error" or "warning"
    path: Path | None = None
    feature: str = ""
    message: str

    def __str__(self) -> str:
        if self.path is not None:
            return f"{self.level}: {self.path}: {self.message}"
        return f"{self.level}: {self.message}"


class Project(BaseModel):
    """The whole intentc project loaded into memory."""

    project_intent: ProjectIntent
    implementations: dict[str, Implementation] = Field(default_factory=dict)
    assertions: list[ValidationFile] = Field(default_factory=list)
    features: dict[str, FeatureNode] = Field(default_factory=dict)
    intent_dir: Path | None = None

    # -- implementations -----------------------------------------------------

    def resolve_implementation(self, name: str | None = None) -> Implementation | None:
        """Pick an implementation. Raises KeyError (unknown) or ValueError (ambiguous)."""
        if name is not None:
            if name not in self.implementations:
                available = ", ".join(sorted(self.implementations)) or "(none)"
                raise KeyError(f"Implementation '{name}' not found. Available: {available}")
            return self.implementations[name]
        if not self.implementations:
            return None
        if len(self.implementations) == 1:
            return next(iter(self.implementations.values()))
        if "default" in self.implementations:
            return self.implementations["default"]
        raise ValueError(
            "Ambiguous implementation: several exist and none is named 'default' "
            f"({', '.join(sorted(self.implementations))}). Pass --implementation <name>."
        )

    # -- DAG -----------------------------------------------------------------

    def _require_feature(self, feature_path: str) -> None:
        if feature_path in self.features:
            return
        available = sorted(self.features)
        close = difflib.get_close_matches(feature_path, available, n=1)
        hint = f" Did you mean '{close[0]}'?" if close else ""
        listing = ", ".join(available) if available else "(none)"
        raise KeyError(f"Feature '{feature_path}' not found.{hint} Available: {listing}")

    def parents(self, feature_path: str) -> list[str]:
        """Direct dependencies of a feature."""
        self._require_feature(feature_path)
        return list(self.features[feature_path].depends_on)

    def ancestors(self, feature_path: str) -> set[str]:
        """All transitive dependencies (BFS)."""
        self._require_feature(feature_path)
        seen: set[str] = set()
        queue: deque[str] = deque(self.features[feature_path].depends_on)
        while queue:
            dep = queue.popleft()
            if dep in seen:
                continue
            seen.add(dep)
            if dep in self.features:
                queue.extend(self.features[dep].depends_on)
        return seen

    def children(self, feature_path: str) -> list[str]:
        """Features that directly depend on this feature."""
        self._require_feature(feature_path)
        return [fp for fp, node in self.features.items() if feature_path in node.depends_on]

    def descendants(self, feature_path: str) -> set[str]:
        """All features that transitively depend on this feature (BFS)."""
        self._require_feature(feature_path)
        seen: set[str] = set()
        queue: deque[str] = deque(self.children(feature_path))
        while queue:
            fp = queue.popleft()
            if fp in seen:
                continue
            seen.add(fp)
            queue.extend(self.children(fp))
        return seen

    def topological_order(self) -> list[str]:
        """Dependency-first order; ties broken by feature path. Raises ValueError on a cycle."""
        in_degree = {fp: 0 for fp in self.features}
        for fp, node in self.features.items():
            for dep in node.depends_on:
                if dep in in_degree:
                    in_degree[fp] += 1
        ready = sorted(fp for fp, deg in in_degree.items() if deg == 0)
        order: list[str] = []
        while ready:
            fp = ready.pop(0)
            order.append(fp)
            newly_ready = []
            for child in self.children(fp):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    newly_ready.append(child)
            ready = sorted(ready + newly_ready)
        if len(order) != len(self.features):
            stuck = sorted(set(self.features) - set(order))
            raise ValueError(f"Dependency cycle detected involving: {', '.join(stuck)}")
        return order

    def buildable_after(self, built: set[str]) -> list[str]:
        """Features not yet built whose direct dependencies are all built, in topological order."""
        return [
            fp
            for fp in self.topological_order()
            if fp not in built and all(dep in built for dep in self.features[fp].depends_on)
        ]

    def source_files(self, feature_path: str) -> list[Path]:
        """All .ic and .icv source paths for a feature, sorted."""
        self._require_feature(feature_path)
        node = self.features[feature_path]
        paths = [i.source_path for i in node.intents if i.source_path is not None]
        paths += [v.source_path for v in node.validations if v.source_path is not None]
        return sorted(paths)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _is_hidden(rel: Path) -> bool:
    return any(part.startswith((".", "_")) for part in rel.parts)


def _feature_path(rel: Path) -> str:
    return "/".join(rel.parent.parts)


def load_project(intent_dir: Path) -> Project:
    """Load a project from an ``intent/`` directory. Raises ParseErrors with all problems."""
    intent_dir = Path(intent_dir)
    errors: list[ParseError] = []

    project_ic = intent_dir / "project.ic"
    if not project_ic.is_file():
        raise ParseErrors(
            [ParseError(project_ic, "project.ic not found — every intentc project needs intent/project.ic")]
        )
    try:
        project_intent = parse_intent_file(project_ic, as_project=True)
    except ParseErrors as exc:
        raise ParseErrors(exc.errors) from exc
    assert isinstance(project_intent, ProjectIntent)

    implementations: dict[str, Implementation] = {}
    impl_dir = intent_dir / "implementations"
    if impl_dir.is_dir():
        for ic in sorted(impl_dir.glob("*.ic")):
            try:
                impl = parse_intent_file(ic, as_implementation=True)
                assert isinstance(impl, Implementation)
                implementations[impl.name] = impl
            except ParseErrors as exc:
                errors.extend(exc.errors)
    legacy = intent_dir / "implementation.ic"
    if legacy.is_file() and not implementations:
        try:
            impl = parse_intent_file(legacy, as_implementation=True)
            assert isinstance(impl, Implementation)
            implementations[impl.name] = impl
        except ParseErrors as exc:
            errors.extend(exc.errors)

    assertions: list[ValidationFile] = []
    assert_dir = intent_dir / "assertions"
    if assert_dir.is_dir():
        for icv in sorted(assert_dir.glob("*.icv")):
            try:
                assertions.append(parse_validation_file(icv))
            except ParseErrors as exc:
                errors.extend(exc.errors)

    features: dict[str, FeatureNode] = {}
    for ic in sorted(intent_dir.rglob("*.ic")):
        rel = ic.relative_to(intent_dir)
        if len(rel.parts) < 2 or rel.parts[0] in _SPECIAL_DIRS or _is_hidden(rel):
            continue
        fp = _feature_path(rel)
        node = features.setdefault(fp, FeatureNode(path=fp))
        try:
            intent = parse_intent_file(ic)
            assert isinstance(intent, IntentFile)
            node.intents.append(intent)
        except ParseErrors as exc:
            errors.extend(exc.errors)

    for icv in sorted(intent_dir.rglob("*.icv")):
        rel = icv.relative_to(intent_dir)
        if len(rel.parts) < 2 or rel.parts[0] in _SPECIAL_DIRS or _is_hidden(rel):
            continue
        fp = _feature_path(rel)
        if fp not in features:
            # Validations with no intent: reported by check_project, not a load error.
            continue
        try:
            features[fp].validations.append(parse_validation_file(icv))
        except ParseErrors as exc:
            errors.extend(exc.errors)

    # Wildcard expansion and dependency resolution.
    all_paths = sorted(features)
    for fp, node in features.items():
        for intent in node.intents:
            expanded: list[str] = []
            for dep in intent.depends_on:
                if any(ch in dep for ch in "*?["):
                    matches = [p for p in all_paths if fnmatch.fnmatchcase(p, dep) and p != fp]
                    if not matches:
                        errors.append(
                            ParseError(
                                intent.source_path,
                                f"wildcard dependency '{dep}' matched no features",
                                field="depends_on",
                            )
                        )
                    expanded.extend(m for m in matches if m not in expanded)
                elif dep not in expanded:
                    expanded.append(dep)
            intent.depends_on = expanded
            for dep in expanded:
                if dep == fp:
                    errors.append(
                        ParseError(intent.source_path, f"feature '{fp}' depends on itself", field="depends_on")
                    )
                elif dep not in features:
                    close = difflib.get_close_matches(dep, all_paths, n=1)
                    hint = f"; did you mean '{close[0]}'?" if close else ""
                    errors.append(
                        ParseError(
                            intent.source_path,
                            f"unknown dependency '{dep}'{hint} (available: {', '.join(all_paths) or 'none'})",
                            field="depends_on",
                        )
                    )

    project = Project(
        project_intent=project_intent,
        implementations=implementations,
        assertions=assertions,
        features=features,
        intent_dir=intent_dir,
    )

    if not errors:
        try:
            project.topological_order()
        except ValueError as exc:
            errors.append(ParseError(intent_dir, str(exc), field="depends_on"))

    if errors:
        raise ParseErrors(errors)
    return project


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------


def check_project(project: Project) -> list[ProjectIssue]:
    """Deterministic lint of a loaded project. Never calls an agent."""
    issues: list[ProjectIssue] = []

    def add(level: str, path: Path | None, feature: str, message: str) -> None:
        issues.append(ProjectIssue(level=level, path=path, feature=feature, message=message))

    if not project.implementations:
        add(
            "warning",
            (project.intent_dir / "implementations") if project.intent_dir else None,
            "",
            "no implementation found — add intent/implementations/default.ic describing the language and stack",
        )

    known_targets = set(project.features) | {"project"}

    if project.intent_dir is not None:
        for icv in sorted(project.intent_dir.rglob("*.icv")):
            rel = icv.relative_to(project.intent_dir)
            if len(rel.parts) < 2 or rel.parts[0] in _SPECIAL_DIRS or _is_hidden(rel):
                continue
            fp = _feature_path(rel)
            if fp not in project.features:
                add(
                    "error",
                    icv,
                    fp,
                    f"validation file has no intent to validate — add {fp}/{rel.parent.name}.ic or remove it",
                )

    for vf in project.assertions:
        if vf.target and vf.target != "project" and vf.target not in known_targets:
            add("error", vf.source_path, "", f"target '{vf.target}' is not a feature — use 'project' for assertions")

    for fp, node in project.features.items():
        leaf = fp.rsplit("/", 1)[-1]
        for intent in node.intents:
            if intent.name != leaf:
                add(
                    "warning",
                    intent.source_path,
                    fp,
                    f"name '{intent.name}' differs from the directory '{leaf}' — rename to `name: {leaf}`",
                )
            if not intent.body.strip():
                add("warning", intent.source_path, fp, "empty intent body — describe what to build")
            if intent.source_path is not None:
                base = intent.source_path.parent
                for ref in intent.file_references:
                    if "*" in ref:
                        if not list(base.glob(ref)):
                            add("warning", intent.source_path, fp, f"referenced files '{ref}' match nothing")
                    elif not (base / ref).exists():
                        add("warning", intent.source_path, fp, f"referenced file '{ref}' does not exist")

        if not any(vf.validations for vf in node.validations):
            add(
                "warning",
                node.intents[0].source_path if node.intents else None,
                fp,
                f"no validations — add a validations.icv next to the intent so the build can be checked",
            )

        for vf in node.validations:
            if vf.target and vf.target not in known_targets:
                add("error", vf.source_path, fp, f"target '{vf.target}' is not a feature — use `target: {fp}`")
            elif vf.target and vf.target != fp:
                add(
                    "warning",
                    vf.source_path,
                    fp,
                    f"target '{vf.target}' differs from its directory — use `target: {fp}`",
                )
            for v in vf.validations:
                if v.type == ValidationType.AGENT_VALIDATION.value:
                    rubric = str(v.args.get("rubric", "")).strip()
                    if len(rubric) < _MIN_RUBRIC_LENGTH:
                        add(
                            "warning",
                            vf.source_path,
                            fp,
                            f"validation '{v.name}' has a very short rubric — say precisely what must be true",
                        )

    order = {"error": 0, "warning": 1}
    issues.sort(key=lambda i: (str(i.path or ""), order.get(i.level, 2), i.message))
    return issues


# ---------------------------------------------------------------------------
# Writing and blank projects
# ---------------------------------------------------------------------------


def write_project(project: Project, dest_dir: Path) -> Path:
    """Write a project (and referenced supporting files) to ``dest_dir``."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    write_intent_file(project.project_intent, dest_dir / "project.ic")
    for impl in project.implementations.values():
        write_intent_file(impl, dest_dir / "implementations" / f"{impl.name}.ic")
    for index, vf in enumerate(project.assertions):
        name = vf.source_path.name if vf.source_path else f"assertion_{index}.icv"
        write_validation_file(vf, dest_dir / "assertions" / name)

    for fp, node in project.features.items():
        feature_dir = dest_dir / fp
        for intent in node.intents:
            name = intent.source_path.name if intent.source_path else f"{intent.name}.ic"
            write_intent_file(intent, feature_dir / name)
        for vf in node.validations:
            name = vf.source_path.name if vf.source_path else "validation.icv"
            write_validation_file(vf, feature_dir / name)
        for intent in node.intents:
            if intent.source_path is None:
                continue
            base = intent.source_path.parent
            for ref in intent.file_references:
                sources = list(base.glob(ref)) if "*" in ref else [base / ref]
                for src in sources:
                    if not src.is_file():
                        continue
                    try:
                        rel = src.resolve().relative_to(base.resolve())
                    except ValueError:
                        continue  # references outside the feature directory are not copied
                    dest = feature_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if not dest.exists():
                        shutil.copy2(src, dest)
    return dest_dir


def blank_project(name: str) -> Project:
    """A minimal starter project: project.ic, implementations/default.ic, one starter feature."""
    project_intent = ProjectIntent(
        name=name,
        body=(
            f"# {name}\n\n"
            "Describe what this project is and why it exists. This document is included in every\n"
            "build, so capture the purpose, the users, and the principles that should guide every feature.\n"
        ),
    )
    impl = Implementation(
        name="default",
        tags=["implementation"],
        body=(
            "# Default Implementation\n\n"
            "Describe how the project is built: language, framework, packaging, conventions,\n"
            "and where generated files go. The agent reads this for every feature.\n"
        ),
    )
    starter = IntentFile(
        name="starter",
        body=(
            "# Starter Feature\n\n"
            "Describe your first feature here: what it does, its inputs and outputs, and how it\n"
            "should be organised in the output directory. Add more features as sibling directories\n"
            "and reference them with `depends_on`.\n\n"
            "Run `intentc check` to lint the project and `intentc build` to generate it.\n"
        ),
    )
    starter_validation = ValidationFile(
        target="starter",
        validations=[
            Validation(
                name="starter-output-exists",
                type=ValidationType.FILE_EXISTS.value,
                severity=Severity.ERROR,
                args={"paths": ["*"]},
            ),
        ],
    )
    return Project(
        project_intent=project_intent,
        implementations={"default": impl},
        features={"starter": FeatureNode(path="starter", intents=[starter], validations=[starter_validation])},
    )
