"""Load, write, and traverse an intentc project as a dependency DAG."""

from __future__ import annotations

import shutil
from collections import deque
from pathlib import Path

from pydantic import BaseModel

from intentc.core.parser import (
    ParseError,
    ParseErrors,
    parse_intent_file,
    parse_validation_file,
    write_intent_file,
    write_validation_file,
)
from intentc.core.types import (
    Implementation,
    IntentFile,
    ProjectIntent,
    ValidationFile,
)


class FeatureNode(BaseModel):
    """A feature in the project DAG — corresponds to a directory under intent/."""

    model_config = {"extra": "ignore"}

    path: str
    intents: list[IntentFile] = []
    validations: list[ValidationFile] = []

    @property
    def depends_on(self) -> list[str]:
        """Combined dependencies from all intent files in this feature."""
        seen: set[str] = set()
        deps: list[str] = []
        for intent in self.intents:
            for dep in intent.depends_on:
                if dep not in seen:
                    deps.append(dep)
                    seen.add(dep)
        return deps


class Project(BaseModel):
    """The full intentc project parsed from an intent/ directory.

    Provides DAG traversal over features and their dependencies.
    """

    model_config = {"extra": "ignore"}

    project_intent: ProjectIntent
    implementation: Implementation | None = None
    assertions: list[ValidationFile] = []
    features: dict[str, FeatureNode] = {}
    intent_dir: Path | None = None

    def parents(self, feature_path: str) -> list[str]:
        """Direct dependencies of a feature."""
        self._require_feature(feature_path)
        return list(self.features[feature_path].depends_on)

    def ancestors(self, feature_path: str) -> set[str]:
        """All transitive dependencies of a feature."""
        self._require_feature(feature_path)
        result: set[str] = set()
        queue = deque(self.features[feature_path].depends_on)
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
            fp
            for fp, node in self.features.items()
            if feature_path in node.depends_on
        ]

    def descendants(self, feature_path: str) -> set[str]:
        """All features that transitively depend on this feature."""
        self._require_feature(feature_path)
        result: set[str] = set()
        queue = deque(self.children(feature_path))
        while queue:
            fp = queue.popleft()
            if fp in result:
                continue
            result.add(fp)
            queue.extend(self.children(fp))
        return result

    def topological_order(self) -> list[str]:
        """Return feature paths in dependency-first topological order.

        Raises:
            ValueError: If the graph contains a cycle.
        """
        in_degree: dict[str, int] = {fp: 0 for fp in self.features}
        adj: dict[str, list[str]] = {fp: [] for fp in self.features}

        for fp, node in self.features.items():
            for dep in node.depends_on:
                if dep in self.features:
                    adj[dep].append(fp)
                    in_degree[fp] += 1

        queue = deque(fp for fp, deg in in_degree.items() if deg == 0)
        order: list[str] = []

        while queue:
            fp = queue.popleft()
            order.append(fp)
            for child in adj[fp]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(order) != len(self.features):
            remaining = sorted(set(self.features) - set(order))
            raise ValueError(
                f"Dependency cycle detected among features: {', '.join(remaining)}"
            )

        return order

    def _require_feature(self, feature_path: str) -> None:
        if feature_path not in self.features:
            available = ", ".join(sorted(self.features)) or "(none)"
            raise KeyError(
                f"Feature '{feature_path}' not found in project. "
                f"Available features: {available}"
            )


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def load_project(intent_dir: Path) -> Project:
    """Load an entire intentc project from its intent/ directory.

    Parses all .ic and .icv files, builds the feature DAG, and validates
    the dependency graph. Accumulates all parsing errors and raises them
    together.

    Raises:
        ParseErrors: If any files fail to parse (all errors accumulated).
    """
    intent_dir = Path(intent_dir)
    errors: list[ParseError] = []

    if not intent_dir.is_dir():
        raise ParseErrors(
            [ParseError(path=intent_dir, field=None, message="Intent directory not found")]
        )

    # --- project.ic (required) ---
    project_path = intent_dir / "project.ic"
    project_intent: ProjectIntent | None = None
    if not project_path.exists():
        errors.append(
            ParseError(
                path=project_path,
                field=None,
                message="Required file project.ic not found. "
                "Run 'intentc init' to create a new project.",
            )
        )
    else:
        try:
            project_intent = parse_intent_file(project_path, as_project=True)  # type: ignore[assignment]
        except ParseErrors as exc:
            errors.extend(exc.errors)

    # --- implementation.ic (optional) ---
    impl_path = intent_dir / "implementation.ic"
    implementation: Implementation | None = None
    if impl_path.exists():
        try:
            implementation = parse_intent_file(impl_path, as_implementation=True)  # type: ignore[assignment]
        except ParseErrors as exc:
            errors.extend(exc.errors)

    # --- assertions/*.icv (optional) ---
    assertions: list[ValidationFile] = []
    assertions_dir = intent_dir / "assertions"
    if assertions_dir.is_dir():
        for icv_path in sorted(assertions_dir.glob("*.icv")):
            try:
                assertions.append(parse_validation_file(icv_path))
            except ParseErrors as exc:
                errors.extend(exc.errors)

    # --- Feature directories (everything else) ---
    features: dict[str, FeatureNode] = {}
    skip_dirs = {intent_dir, assertions_dir}

    for ic_path in sorted(intent_dir.rglob("*.ic")):
        if ic_path.parent in skip_dirs:
            continue

        feature_path = str(ic_path.parent.relative_to(intent_dir))

        if feature_path not in features:
            features[feature_path] = FeatureNode(path=feature_path)

        try:
            intent = parse_intent_file(ic_path)
            features[feature_path].intents.append(intent)
        except ParseErrors as exc:
            errors.extend(exc.errors)

    for icv_path in sorted(intent_dir.rglob("*.icv")):
        if icv_path.parent in skip_dirs:
            continue

        # Skip empty placeholder files
        if icv_path.stat().st_size == 0:
            continue

        feature_path = str(icv_path.parent.relative_to(intent_dir))

        if feature_path not in features:
            features[feature_path] = FeatureNode(path=feature_path)

        try:
            vf = parse_validation_file(icv_path)
            features[feature_path].validations.append(vf)
        except ParseErrors as exc:
            errors.extend(exc.errors)

    # --- Validate dependency references ---
    for fp, node in features.items():
        for dep in node.depends_on:
            if dep not in features:
                for intent in node.intents:
                    if dep in intent.depends_on:
                        errors.append(
                            ParseError(
                                path=intent.source_path or Path(fp),
                                field="depends_on",
                                message=f"Dependency '{dep}' not found. "
                                f"Available features: {', '.join(sorted(features))}",
                            )
                        )
                        break

    if errors:
        raise ParseErrors(errors)

    assert project_intent is not None

    project = Project(
        project_intent=project_intent,
        implementation=implementation,
        assertions=assertions,
        features=features,
        intent_dir=intent_dir,
    )

    # --- Check for cycles ---
    try:
        project.topological_order()
    except ValueError as exc:
        raise ParseErrors(
            [ParseError(path=intent_dir, field=None, message=str(exc))]
        )

    return project


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_project(project: Project, dest_dir: Path) -> Path:
    """Write an entire project to a new intent/ directory.

    Writes all intent files, validation files, and copies referenced
    supporting files to preserve relative references.

    Returns:
        The destination directory.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    write_intent_file(project.project_intent, dest_dir / "project.ic")
    _copy_file_references(project.project_intent, dest_dir)

    if project.implementation is not None:
        write_intent_file(project.implementation, dest_dir / "implementation.ic")
        _copy_file_references(project.implementation, dest_dir)

    for vf in project.assertions:
        if vf.source_path:
            dest_path = dest_dir / "assertions" / vf.source_path.name
        else:
            dest_path = dest_dir / "assertions" / f"{vf.target.replace('/', '_')}.icv"
        write_validation_file(vf, dest_path)

    for fp, node in project.features.items():
        feature_dir = dest_dir / fp

        for intent in node.intents:
            if intent.source_path:
                dest_path = feature_dir / intent.source_path.name
            else:
                dest_path = feature_dir / f"{intent.name}.ic"
            write_intent_file(intent, dest_path)
            _copy_file_references(intent, feature_dir)

        for vf in node.validations:
            if vf.source_path:
                dest_path = feature_dir / vf.source_path.name
            else:
                dest_path = feature_dir / f"{vf.target.replace('/', '_')}.icv"
            write_validation_file(vf, dest_path)

    return dest_dir


def _copy_file_references(
    intent: IntentFile | ProjectIntent | Implementation,
    dest_dir: Path,
) -> None:
    """Copy files referenced in an intent body to the destination directory."""
    if not intent.file_references or not intent.source_path:
        return

    source_dir = intent.source_path.parent

    for ref in intent.file_references:
        src = (source_dir / ref).resolve()
        if not src.exists():
            continue
        dest = (dest_dir / ref).resolve()
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


# ---------------------------------------------------------------------------
# Blank project
# ---------------------------------------------------------------------------


def blank_project(name: str) -> Project:
    """Create a minimal blank project ready to be written to disk.

    Returns a Project with a project.ic and a single starter feature.
    """
    project_intent = ProjectIntent(
        name=name,
        tags=["project"],
        body=f"# {name}\n\nDescribe your project here.",
    )

    starter = IntentFile(
        name="starter",
        body="# Starter Feature\n\nDescribe what this feature should do.",
    )

    return Project(
        project_intent=project_intent,
        features={
            "starter": FeatureNode(
                path="starter",
                intents=[starter],
            ),
        },
    )
