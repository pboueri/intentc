package graph

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
				err := dag.AddEdge(edge[0], edge[1])
				if err != nil {
					t.Fatalf("Failed to add edge %s -> %s: %v", edge[0], edge[1], err)
				}
			}
			
			hasCycle, cycle := dag.DetectCycles()
			
			if hasCycle != tt.wantCycle {
				t.Errorf("DetectCycles() = %v, want %v", hasCycle, tt.wantCycle)
			}
			
			if hasCycle && tt.cycleLen > 0 {
				// The cycle includes the starting node twice (at beginning and end)
				// So the actual unique nodes in cycle is len(cycle) - 1
				if len(cycle)-1 != tt.cycleLen {
					t.Errorf("Cycle length = %d (cycle: %v), want %d", len(cycle)-1, cycle, tt.cycleLen)
				}
			}
		})
	}
}

func TestDAG_TopologicalSort(t *testing.T) {
	dag := NewDAG()
	
	// Create a simple DAG: A -> B -> C, A -> C
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
	
	// Create a map of positions
	positions := make(map[string]int)
	for i, node := range sorted {
		positions[node.Name] = i
	}
	
	// Verify ordering: C should come before B and A
	if positions["C"] >= positions["B"] {
		t.Errorf("C should come before B in topological sort")
	}
	if positions["C"] >= positions["A"] {
		t.Errorf("C should come before A in topological sort")
	}
	if positions["B"] >= positions["A"] {
		t.Errorf("B should come before A in topological sort")
	}
}

func TestDAG_TopologicalSort_WithCycle(t *testing.T) {
	dag := NewDAG()
	
	// Create a cycle
	dag.AddEdge("A", "B")
	dag.AddEdge("B", "C")
	dag.AddEdge("C", "A")
	
	_, err := dag.TopologicalSort()
	if err == nil {
		t.Error("TopologicalSort should fail with a cycle")
	}
	
	if !strings.Contains(err.Error(), "dependency cycle detected") {
		t.Errorf("Expected cycle detection error, got: %v", err)
	}
}

func TestDAG_FindAffectedNodes(t *testing.T) {
	dag := NewDAG()
	
	// Create a DAG: A -> B -> D, A -> C -> D, E standalone
	dag.AddEdge("A", "B")
	dag.AddEdge("B", "D")
	dag.AddEdge("A", "C")
	dag.AddEdge("C", "D")
	dag.AddNode("E", nil)
	
	// Changing B should affect B and A
	affected := dag.FindAffectedNodes("B")
	if len(affected) != 2 {
		t.Errorf("Expected 2 affected nodes for B, got %d", len(affected))
	}
	
	affectedNames := make(map[string]bool)
	for _, node := range affected {
		affectedNames[node.Name] = true
	}
	
	if !affectedNames["A"] || !affectedNames["B"] {
		t.Errorf("Expected A and B to be affected, got %v", affectedNames)
	}
	
	// Changing D should affect D, B, C, and A
	affected = dag.FindAffectedNodes("D")
	if len(affected) != 4 {
		t.Errorf("Expected 4 affected nodes for D, got %d", len(affected))
	}
	
	// Changing E should only affect E
	affected = dag.FindAffectedNodes("E")
	if len(affected) != 1 {
		t.Errorf("Expected 1 affected node for E, got %d", len(affected))
	}
	
	// Non-existent node should return empty
	affected = dag.FindAffectedNodes("X")
	if len(affected) != 0 {
		t.Errorf("Expected 0 affected nodes for non-existent node, got %d", len(affected))
	}
}

func TestDAG_Visualize(t *testing.T) {
	dag := NewDAG()
	
	// Create a simple tree
	dag.AddEdge("root", "child1")
	dag.AddEdge("root", "child2")
	dag.AddEdge("child1", "grandchild")
	
	viz := dag.Visualize()
	
	// Should contain the header
	if !strings.Contains(viz, "Dependency Graph:") {
		t.Error("Visualization should contain header")
	}
	
	// Should contain all nodes
	if !strings.Contains(viz, "root") {
		t.Error("Visualization should contain root")
	}
	if !strings.Contains(viz, "child1") {
		t.Error("Visualization should contain child1")
	}
	if !strings.Contains(viz, "child2") {
		t.Error("Visualization should contain child2")
	}
	if !strings.Contains(viz, "grandchild") {
		t.Error("Visualization should contain grandchild")
	}
}