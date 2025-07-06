package intent

import (
	"strings"
	"testing"
)

func TestDAG_AddNodesAndEdges(t *testing.T) {
	dag := NewDAG()

	// Add nodes
	nodeA := dag.AddNode("A", nil)
	nodeB := dag.AddNode("B", nil)
	dag.AddNode("C", nil)

	if len(dag.nodes) != 3 {
		t.Errorf("Expected 3 nodes, got %d", len(dag.nodes))
	}

	// Add edges
	err := dag.AddEdge("A", "B")
	if err != nil {
		t.Errorf("AddEdge failed: %v", err)
	}

	err = dag.AddEdge("A", "C")
	if err != nil {
		t.Errorf("AddEdge failed: %v", err)
	}

	// Check dependencies
	if len(nodeA.Dependencies) != 2 {
		t.Errorf("Node A should have 2 dependencies, got %d", len(nodeA.Dependencies))
	}

	if len(nodeB.Dependents) != 1 {
		t.Errorf("Node B should have 1 dependent, got %d", len(nodeB.Dependents))
	}

	// Check roots
	roots := dag.GetRoots()
	if len(roots) != 2 {
		t.Errorf("Expected 2 roots (B and C), got %d", len(roots))
	}
}

func TestDAG_DetectCycles(t *testing.T) {
	tests := []struct {
		name      string
		edges     [][2]string
		wantCycle bool
		cycleLen  int
	}{
		{
			name: "no cycle",
			edges: [][2]string{
				{"A", "B"},
				{"B", "C"},
				{"A", "C"},
			},
			wantCycle: false,
		},
		{
			name: "simple cycle",
			edges: [][2]string{
				{"A", "B"},
				{"B", "C"},
				{"C", "A"},
			},
			wantCycle: true,
			cycleLen:  3,
		},
		{
			name: "self loop",
			edges: [][2]string{
				{"A", "A"},
			},
			wantCycle: true,
			cycleLen:  1,
		},
		{
			name: "complex graph with cycle",
			edges: [][2]string{
				{"A", "B"},
				{"B", "C"},
				{"C", "D"},
				{"D", "B"}, // Cycle: B -> C -> D -> B
				{"A", "E"},
			},
			wantCycle: true,
			cycleLen:  3,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dag := NewDAG()
			
			// Add all edges
			for _, edge := range tt.edges {
				dag.AddEdge(edge[0], edge[1])
			}

			hasCycle, cycle := dag.DetectCycles()
			if hasCycle != tt.wantCycle {
				t.Errorf("DetectCycles() = %v, want %v", hasCycle, tt.wantCycle)
			}

			if hasCycle && tt.cycleLen > 0 {
				// Cycle should include the repeated node
				if len(cycle) != tt.cycleLen+1 {
					t.Errorf("Cycle length = %d, want %d", len(cycle)-1, tt.cycleLen)
				}
			}
		})
	}
}

func TestDAG_TopologicalSort(t *testing.T) {
	dag := NewDAG()

	// Build a DAG: A -> B -> C, A -> C
	dag.AddEdge("A", "B")
	dag.AddEdge("B", "C")
	dag.AddEdge("A", "C")

	sorted, err := dag.TopologicalSort()
	if err != nil {
		t.Fatalf("TopologicalSort failed: %v", err)
	}

	if len(sorted) != 3 {
		t.Errorf("Expected 3 nodes in sorted order, got %d", len(sorted))
	}

	// Build a map of positions
	positions := make(map[string]int)
	for i, node := range sorted {
		positions[node.Name] = i
	}

	// Check ordering constraints
	if positions["C"] >= positions["B"] {
		t.Error("C should come before B in topological order")
	}
	if positions["C"] >= positions["A"] {
		t.Error("C should come before A in topological order")
	}
	if positions["B"] >= positions["A"] {
		t.Error("B should come before A in topological order")
	}
}

func TestDAG_TopologicalSort_WithCycle(t *testing.T) {
	dag := NewDAG()

	// Build a graph with cycle
	dag.AddEdge("A", "B")
	dag.AddEdge("B", "C")
	dag.AddEdge("C", "A")

	_, err := dag.TopologicalSort()
	if err == nil {
		t.Error("TopologicalSort should fail with cycle")
	}

	if !strings.Contains(err.Error(), "cycle detected") {
		t.Errorf("Expected cycle error, got: %v", err)
	}
}

func TestDAG_FindAffectedNodes(t *testing.T) {
	dag := NewDAG()

	// Build a DAG
	dag.AddEdge("A", "B")
	dag.AddEdge("B", "C")
	dag.AddEdge("B", "D")
	dag.AddEdge("E", "D")

	// Changes to C should affect A and B
	affected := dag.FindAffectedNodes("C")
	affectedNames := make(map[string]bool)
	for _, node := range affected {
		affectedNames[node.Name] = true
	}

	expected := map[string]bool{"A": true, "B": true, "C": true}
	if len(affected) != len(expected) {
		t.Errorf("Expected %d affected nodes, got %d", len(expected), len(affected))
	}

	for name := range expected {
		if !affectedNames[name] {
			t.Errorf("Expected %s to be affected", name)
		}
	}
}

func TestDAG_BuildFromIntents(t *testing.T) {
	dag := NewDAG()

	intents := []*IntentFile{
		{Name: "auth", Dependencies: []string{"user", "database"}},
		{Name: "user", Dependencies: []string{"database"}},
		{Name: "database", Dependencies: []string{}},
		{Name: "api", Dependencies: []string{"auth"}},
	}

	err := dag.BuildFromIntents(intents)
	if err != nil {
		t.Fatalf("BuildFromIntents failed: %v", err)
	}

	// Check nodes
	if len(dag.nodes) != 4 {
		t.Errorf("Expected 4 nodes, got %d", len(dag.nodes))
	}

	// Check dependencies
	authNode, _ := dag.GetNode("auth")
	if len(authNode.Dependencies) != 2 {
		t.Errorf("Auth should have 2 dependencies, got %d", len(authNode.Dependencies))
	}

	// Check roots
	roots := dag.GetRoots()
	if len(roots) != 1 || roots[0].Name != "database" {
		t.Error("Database should be the only root")
	}
}

func TestDAG_BuildFromIntents_MissingDependency(t *testing.T) {
	dag := NewDAG()

	intents := []*IntentFile{
		{Name: "auth", Dependencies: []string{"missing-dep"}},
	}

	err := dag.BuildFromIntents(intents)
	if err == nil {
		t.Error("BuildFromIntents should fail with missing dependency")
	}

	if !strings.Contains(err.Error(), "missing intent file for missing-dep") {
		t.Errorf("Expected missing dependency error, got: %v", err)
	}
}

func TestDAG_Visualize(t *testing.T) {
	dag := NewDAG()

	// Build a simple DAG
	dag.AddEdge("api", "auth")
	dag.AddEdge("auth", "user")
	dag.AddEdge("auth", "database")
	dag.AddEdge("user", "database")

	viz := dag.Visualize()
	
	// Check that visualization contains expected elements
	if !strings.Contains(viz, "Dependency Graph:") {
		t.Error("Visualization should contain header")
	}

	if !strings.Contains(viz, "database") {
		t.Error("Visualization should contain database node")
	}

	if !strings.Contains(viz, "└──") || !strings.Contains(viz, "├──") {
		t.Error("Visualization should contain tree characters")
	}
}

func TestDAG_VisualizeWithStatus(t *testing.T) {
	dag := NewDAG()

	// Build a simple DAG
	dag.AddEdge("api", "auth")
	dag.AddEdge("auth", "database")

	// Status function
	statusFunc := func(name string) string {
		switch name {
		case "database":
			return "built"
		case "auth":
			return "building"
		case "api":
			return "failed"
		default:
			return "pending"
		}
	}

	viz := dag.VisualizeWithStatus(statusFunc)

	// Check status indicators
	if !strings.Contains(viz, "[✓]") {
		t.Error("Visualization should contain built indicator")
	}
	if !strings.Contains(viz, "[◐]") {
		t.Error("Visualization should contain building indicator")
	}
	if !strings.Contains(viz, "[✗]") {
		t.Error("Visualization should contain failed indicator")
	}
}