"""Comprehensive tests for the graph (DAG) package."""

import pytest

from core.types import Intent, Target, TargetStatus
from graph.dag import DAG, DAGError, new_dag


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_target(name: str, depends_on: list[str] | None = None, status: TargetStatus = TargetStatus.PENDING) -> Target:
    """Create a Target with the given name and dependencies."""
    return Target(
        name=name,
        intent=Intent(name=name, depends_on=depends_on or []),
        status=status,
    )


# ---------------------------------------------------------------------------
# Basic construction
# ---------------------------------------------------------------------------

class TestNewDAG:
    def test_empty_dag(self) -> None:
        dag = new_dag()
        assert dag.nodes == {}
        assert dag.roots == []

    def test_dag_constructor(self) -> None:
        dag = DAG()
        assert dag.nodes == {}
        assert dag.roots == []


class TestAddTarget:
    def test_add_single(self) -> None:
        dag = new_dag()
        t = make_target("auth")
        dag.add_target(t)
        assert "auth" in dag.nodes
        assert dag.nodes["auth"].target is t

    def test_add_multiple(self) -> None:
        dag = new_dag()
        dag.add_target(make_target("a"))
        dag.add_target(make_target("b"))
        dag.add_target(make_target("c"))
        assert len(dag.nodes) == 3

    def test_duplicate_raises(self) -> None:
        dag = new_dag()
        dag.add_target(make_target("auth"))
        with pytest.raises(DAGError, match="duplicate target 'auth'"):
            dag.add_target(make_target("auth"))


# ---------------------------------------------------------------------------
# Resolve
# ---------------------------------------------------------------------------

class TestResolve:
    def test_no_deps(self) -> None:
        dag = new_dag()
        dag.add_target(make_target("a"))
        dag.add_target(make_target("b"))
        dag.resolve()
        assert len(dag.roots) == 2

    def test_simple_chain(self) -> None:
        """A -> B -> C (A depends on B, B depends on C)."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b"]))
        dag.add_target(make_target("b", ["c"]))
        dag.add_target(make_target("c"))
        dag.resolve()
        assert len(dag.roots) == 1
        assert dag.roots[0].name == "c"
        assert len(dag.nodes["a"].dependencies) == 1
        assert dag.nodes["a"].dependencies[0].name == "b"
        assert len(dag.nodes["b"].dependents) == 1
        assert dag.nodes["b"].dependents[0].name == "a"

    def test_unknown_dependency(self) -> None:
        dag = new_dag()
        dag.add_target(make_target("auth", ["user-model"]))
        with pytest.raises(DAGError, match="target 'auth' depends on unknown target 'user-model'"):
            dag.resolve()

    def test_self_dependency(self) -> None:
        dag = new_dag()
        dag.add_target(make_target("auth", ["auth"]))
        with pytest.raises(DAGError, match="target 'auth' depends on itself"):
            dag.resolve()

    def test_diamond(self) -> None:
        """Diamond: A->B, A->C, B->D, C->D."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b", "c"]))
        dag.add_target(make_target("b", ["d"]))
        dag.add_target(make_target("c", ["d"]))
        dag.add_target(make_target("d"))
        dag.resolve()
        assert len(dag.roots) == 1
        assert dag.roots[0].name == "d"
        # D should have two dependents: B and C
        dep_names = sorted(n.name for n in dag.nodes["d"].dependents)
        assert dep_names == ["b", "c"]

    def test_resolve_idempotent(self) -> None:
        """Calling resolve twice should not duplicate edges."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b"]))
        dag.add_target(make_target("b"))
        dag.resolve()
        dag.resolve()
        assert len(dag.nodes["a"].dependencies) == 1
        assert len(dag.nodes["b"].dependents) == 1


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------

class TestDetectCycles:
    def test_no_cycle(self) -> None:
        dag = new_dag()
        dag.add_target(make_target("a", ["b"]))
        dag.add_target(make_target("b"))
        dag.resolve()
        dag.detect_cycles()  # should not raise

    def test_simple_cycle(self) -> None:
        """A -> B -> A."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b"]))
        dag.add_target(make_target("b", ["a"]))
        dag.resolve()
        with pytest.raises(DAGError, match="dependency cycle detected"):
            dag.detect_cycles()

    def test_three_node_cycle(self) -> None:
        """A -> B -> C -> A."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b"]))
        dag.add_target(make_target("b", ["c"]))
        dag.add_target(make_target("c", ["a"]))
        dag.resolve()
        with pytest.raises(DAGError, match="dependency cycle detected"):
            dag.detect_cycles()

    def test_cycle_error_contains_path(self) -> None:
        """Verify the error message includes the cycle path with arrows."""
        dag = new_dag()
        dag.add_target(make_target("auth", ["session"]))
        dag.add_target(make_target("session", ["auth"]))
        dag.resolve()
        with pytest.raises(DAGError, match=r"->"):
            dag.detect_cycles()


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------

class TestTopologicalSort:
    def test_empty_graph(self) -> None:
        dag = new_dag()
        assert dag.topological_sort() == []

    def test_single_node(self) -> None:
        dag = new_dag()
        t = make_target("a")
        dag.add_target(t)
        dag.resolve()
        result = dag.topological_sort()
        assert len(result) == 1
        assert result[0].name == "a"

    def test_linear_chain(self) -> None:
        """A depends on B depends on C. Build order: C, B, A."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b"]))
        dag.add_target(make_target("b", ["c"]))
        dag.add_target(make_target("c"))
        dag.resolve()
        result = dag.topological_sort()
        names = [t.name for t in result]
        assert names == ["c", "b", "a"]

    def test_diamond_order(self) -> None:
        """Diamond: D before B and C, both before A."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b", "c"]))
        dag.add_target(make_target("b", ["d"]))
        dag.add_target(make_target("c", ["d"]))
        dag.add_target(make_target("d"))
        dag.resolve()
        result = dag.topological_sort()
        names = [t.name for t in result]
        # D must come before B and C; B and C before A
        assert names.index("d") < names.index("b")
        assert names.index("d") < names.index("c")
        assert names.index("b") < names.index("a")
        assert names.index("c") < names.index("a")

    def test_independent_nodes(self) -> None:
        """Nodes with no deps are returned in alphabetical order."""
        dag = new_dag()
        dag.add_target(make_target("z"))
        dag.add_target(make_target("a"))
        dag.add_target(make_target("m"))
        dag.resolve()
        result = dag.topological_sort()
        names = [t.name for t in result]
        assert names == ["a", "m", "z"]

    def test_cycle_raises_in_topo_sort(self) -> None:
        dag = new_dag()
        dag.add_target(make_target("a", ["b"]))
        dag.add_target(make_target("b", ["a"]))
        dag.resolve()
        with pytest.raises(DAGError, match="dependency cycle detected"):
            dag.topological_sort()


# ---------------------------------------------------------------------------
# get_affected
# ---------------------------------------------------------------------------

class TestGetAffected:
    def test_leaf_has_no_affected(self) -> None:
        """A node with no dependents returns empty list."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b"]))
        dag.add_target(make_target("b"))
        dag.resolve()
        affected = dag.get_affected("a")
        assert affected == []

    def test_root_affects_all(self) -> None:
        """Changing C (root) affects B and A in a chain A->B->C."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b"]))
        dag.add_target(make_target("b", ["c"]))
        dag.add_target(make_target("c"))
        dag.resolve()
        affected = dag.get_affected("c")
        names = sorted(t.name for t in affected)
        assert names == ["a", "b"]

    def test_diamond_affected(self) -> None:
        """In diamond A->B->D, A->C->D, changing D affects B, C, A."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b", "c"]))
        dag.add_target(make_target("b", ["d"]))
        dag.add_target(make_target("c", ["d"]))
        dag.add_target(make_target("d"))
        dag.resolve()
        affected = dag.get_affected("d")
        names = sorted(t.name for t in affected)
        assert names == ["a", "b", "c"]

    def test_middle_node_affected(self) -> None:
        """In chain A->B->C, changing B only affects A."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b"]))
        dag.add_target(make_target("b", ["c"]))
        dag.add_target(make_target("c"))
        dag.resolve()
        affected = dag.get_affected("b")
        assert len(affected) == 1
        assert affected[0].name == "a"

    def test_unknown_target_raises(self) -> None:
        dag = new_dag()
        with pytest.raises(DAGError, match="unknown target 'ghost'"):
            dag.get_affected("ghost")


# ---------------------------------------------------------------------------
# get_dependency_chain
# ---------------------------------------------------------------------------

class TestGetDependencyChain:
    def test_single_no_deps(self) -> None:
        dag = new_dag()
        dag.add_target(make_target("a"))
        dag.resolve()
        chain = dag.get_dependency_chain("a")
        assert len(chain) == 1
        assert chain[0].name == "a"

    def test_linear_chain(self) -> None:
        """A->B->C. Chain for A is [C, B, A]."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b"]))
        dag.add_target(make_target("b", ["c"]))
        dag.add_target(make_target("c"))
        dag.resolve()
        chain = dag.get_dependency_chain("a")
        names = [t.name for t in chain]
        assert names == ["c", "b", "a"]

    def test_diamond_chain(self) -> None:
        """A->B->D, A->C->D. Chain for A includes D, B, C, A."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b", "c"]))
        dag.add_target(make_target("b", ["d"]))
        dag.add_target(make_target("c", ["d"]))
        dag.add_target(make_target("d"))
        dag.resolve()
        chain = dag.get_dependency_chain("a")
        names = [t.name for t in chain]
        # D before B and C, both before A
        assert names.index("d") < names.index("b")
        assert names.index("d") < names.index("c")
        assert names[-1] == "a"
        assert len(names) == 4

    def test_partial_chain(self) -> None:
        """In diamond, chain for B is [D, B], not including C or A."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b", "c"]))
        dag.add_target(make_target("b", ["d"]))
        dag.add_target(make_target("c", ["d"]))
        dag.add_target(make_target("d"))
        dag.resolve()
        chain = dag.get_dependency_chain("b")
        names = [t.name for t in chain]
        assert names == ["d", "b"]

    def test_unknown_target_raises(self) -> None:
        dag = new_dag()
        with pytest.raises(DAGError, match="unknown target 'ghost'"):
            dag.get_dependency_chain("ghost")


# ---------------------------------------------------------------------------
# Visualize
# ---------------------------------------------------------------------------

class TestVisualize:
    def test_empty_graph(self) -> None:
        dag = new_dag()
        assert dag.visualize() == "(empty graph)"

    def test_single_node(self) -> None:
        dag = new_dag()
        dag.add_target(make_target("api", status=TargetStatus.PENDING))
        dag.resolve()
        output = dag.visualize()
        assert "[ ] api" in output

    def test_status_symbols(self) -> None:
        dag = new_dag()
        dag.add_target(make_target("built", status=TargetStatus.BUILT))
        dag.add_target(make_target("failed", status=TargetStatus.FAILED))
        dag.add_target(make_target("pending", status=TargetStatus.PENDING))
        dag.add_target(make_target("building", status=TargetStatus.BUILDING))
        dag.add_target(make_target("outdated", status=TargetStatus.OUTDATED))
        dag.resolve()
        output = dag.visualize()
        assert "[+] built" in output
        assert "[-] failed" in output
        assert "[ ] pending" in output
        assert "[~] building" in output
        assert "[!] outdated" in output

    def test_chain_visualize(self) -> None:
        """Verify tree structure with connectors for a simple chain."""
        dag = new_dag()
        dag.add_target(make_target("a", ["b"], status=TargetStatus.PENDING))
        dag.add_target(make_target("b", status=TargetStatus.BUILT))
        dag.resolve()
        output = dag.visualize()
        # Root should be b, with a as dependent
        assert "[+] b" in output
        assert "[ ] a" in output

    def test_diamond_visualize(self) -> None:
        dag = new_dag()
        dag.add_target(make_target("a", ["b", "c"]))
        dag.add_target(make_target("b", ["d"]))
        dag.add_target(make_target("c", ["d"]))
        dag.add_target(make_target("d"))
        dag.resolve()
        output = dag.visualize()
        # All nodes should be present
        for name in ("a", "b", "c", "d"):
            assert name in output
        # Tree connector characters should be present
        assert "├" in output or "└" in output
