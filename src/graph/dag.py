"""Directed acyclic graph for target dependency resolution."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.types import Target, TargetStatus


@dataclass
class Node:
    """A node in the dependency graph wrapping a Target."""

    name: str
    target: Target
    dependencies: list[Node] = field(default_factory=list)
    dependents: list[Node] = field(default_factory=list)


STATUS_SYMBOLS: dict[TargetStatus, str] = {
    TargetStatus.BUILT: "+",
    TargetStatus.FAILED: "-",
    TargetStatus.PENDING: " ",
    TargetStatus.BUILDING: "~",
    TargetStatus.OUTDATED: "!",
}


class DAGError(Exception):
    """Raised for graph construction or validation errors."""


class DAG:
    """Directed acyclic graph for dependency resolution among targets."""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.roots: list[Node] = []

    def add_target(self, target: Target) -> None:
        """Add a target as a node. Raises DAGError on duplicate name."""
        name = target.name
        if name in self.nodes:
            raise DAGError(f"graph: duplicate target '{name}'")
        self.nodes[name] = Node(name=name, target=target)

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

    def detect_cycles(self) -> None:
        """Detect cycles via DFS with a recursion stack.

        Raises DAGError with the cycle path if a cycle is found.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {name: WHITE for name in self.nodes}
        parent: dict[str, str | None] = {name: None for name in self.nodes}

        def dfs(name: str) -> None:
            color[name] = GRAY
            node = self.nodes[name]
            for dep in node.dependencies:
                if color[dep.name] == GRAY:
                    # Reconstruct cycle path
                    cycle = [dep.name, name]
                    cur = name
                    while cur != dep.name:
                        cur = parent[cur]  # type: ignore[assignment]
                        if cur is None:
                            break
                        if cur == dep.name:
                            break
                        cycle.append(cur)
                    cycle.reverse()
                    cycle.append(cycle[0])
                    path = " -> ".join(cycle)
                    raise DAGError(
                        f"graph: dependency cycle detected: {path}"
                    )
                if color[dep.name] == WHITE:
                    parent[dep.name] = name
                    dfs(dep.name)
            color[name] = BLACK

        for name in self.nodes:
            if color[name] == WHITE:
                dfs(name)

    def topological_sort(self) -> list[Target]:
        """Return targets in build order (dependencies before dependents).

        Calls detect_cycles first to ensure the graph is a valid DAG.
        """
        self.detect_cycles()

        in_degree: dict[str, int] = {
            name: len(node.dependencies) for name, node in self.nodes.items()
        }
        queue: list[Node] = [
            self.nodes[name] for name, deg in in_degree.items() if deg == 0
        ]
        # Sort queue for deterministic output
        queue.sort(key=lambda n: n.name)

        result: list[Target] = []
        while queue:
            node = queue.pop(0)
            result.append(node.target)
            # Process dependents sorted by name for determinism
            for dep in sorted(node.dependents, key=lambda n: n.name):
                in_degree[dep.name] -= 1
                if in_degree[dep.name] == 0:
                    queue.append(dep)
            queue.sort(key=lambda n: n.name)

        return result

    def get_affected(self, target_name: str) -> list[Target]:
        """Return all transitive dependents of the given target.

        Useful for invalidation: if target_name changes, all returned targets
        are affected and may need rebuilding.

        Raises DAGError if the target is not found.
        """
        if target_name not in self.nodes:
            raise DAGError(f"graph: unknown target '{target_name}'")

        visited: set[str] = set()
        result: list[Target] = []

        def walk(node: Node) -> None:
            for dep in node.dependents:
                if dep.name not in visited:
                    visited.add(dep.name)
                    result.append(dep.target)
                    walk(dep)

        start = self.nodes[target_name]
        walk(start)
        return result

    def get_dependency_chain(self, target_name: str) -> list[Target]:
        """Return the target and all its transitive dependencies in topological order.

        Raises DAGError if the target is not found.
        """
        if target_name not in self.nodes:
            raise DAGError(f"graph: unknown target '{target_name}'")

        visited: set[str] = set()

        def collect(node: Node) -> None:
            if node.name in visited:
                return
            visited.add(node.name)
            for dep in node.dependencies:
                collect(dep)

        start = self.nodes[target_name]
        collect(start)

        # Build a sub-DAG topological sort over collected nodes
        in_degree: dict[str, int] = {}
        for name in visited:
            node = self.nodes[name]
            count = sum(1 for d in node.dependencies if d.name in visited)
            in_degree[name] = count

        queue = sorted(
            [name for name, deg in in_degree.items() if deg == 0]
        )
        result: list[Target] = []
        while queue:
            name = queue.pop(0)
            node = self.nodes[name]
            result.append(node.target)
            for dep in sorted(node.dependents, key=lambda n: n.name):
                if dep.name in in_degree:
                    in_degree[dep.name] -= 1
                    if in_degree[dep.name] == 0:
                        queue.append(dep.name)
            queue.sort()

        return result

    def visualize(self) -> str:
        """Return a human-readable tree representation of the DAG.

        Status symbols: [+] built, [-] failed, [ ] pending, [~] building, [!] outdated
        """
        if not self.nodes:
            return "(empty graph)"

        lines: list[str] = []
        visited: set[str] = set()

        def render(node: Node, prefix: str, is_last: bool, is_root: bool) -> None:
            sym = STATUS_SYMBOLS.get(node.target.status, " ")
            connector = "" if is_root else ("└── " if is_last else "├── ")
            lines.append(f"{prefix}{connector}[{sym}] {node.name}")

            if node.name in visited:
                # Already rendered children elsewhere; skip to avoid duplication
                return
            visited.add(node.name)

            child_prefix = prefix if is_root else (prefix + ("    " if is_last else "│   "))
            dependents = sorted(node.dependents, key=lambda n: n.name)
            for i, child in enumerate(dependents):
                is_last_child = i == len(dependents) - 1
                render(child, child_prefix, is_last_child, False)

        roots = sorted(self.roots, key=lambda n: n.name) if self.roots else []
        # If roots not populated (resolve not called), fall back to all nodes
        if not roots:
            roots = sorted(self.nodes.values(), key=lambda n: n.name)

        for i, root in enumerate(roots):
            render(root, "", i == len(roots) - 1, True)

        return "\n".join(lines)


def new_dag() -> DAG:
    """Create and return an empty DAG."""
    return DAG()
