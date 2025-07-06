package intent

import (
	"fmt"
	"sort"
	"strings"
)

type Node struct {
	Name         string
	Intent       *IntentFile
	Dependencies []*Node
	Dependents   []*Node
	Visited      bool
	InStack      bool
}

type DAG struct {
	nodes map[string]*Node
	roots []*Node
}

func NewDAG() *DAG {
	return &DAG{
		nodes: make(map[string]*Node),
		roots: []*Node{},
	}
}

func (d *DAG) AddNode(name string, intent *IntentFile) *Node {
	if node, exists := d.nodes[name]; exists {
		// Update intent if provided
		if intent != nil {
			node.Intent = intent
		}
		return node
	}
	
	node := &Node{
		Name:         name,
		Intent:       intent,
		Dependencies: []*Node{},
		Dependents:   []*Node{},
	}
	
	d.nodes[name] = node
	d.roots = append(d.roots, node)
	return node
}

func (d *DAG) AddEdge(from, to string) error {
	fromNode := d.AddNode(from, nil)
	toNode := d.AddNode(to, nil)
	
	// Check if edge already exists
	for _, dep := range fromNode.Dependencies {
		if dep.Name == to {
			return nil // Edge already exists
		}
	}
	
	fromNode.Dependencies = append(fromNode.Dependencies, toNode)
	toNode.Dependents = append(toNode.Dependents, fromNode)
	
	// Remove 'from' from roots if it has dependencies
	d.updateRoots()
	
	return nil
}

func (d *DAG) updateRoots() {
	newRoots := []*Node{}
	for _, node := range d.nodes {
		if len(node.Dependencies) == 0 {
			newRoots = append(newRoots, node)
		}
	}
	d.roots = newRoots
}

func (d *DAG) DetectCycles() (bool, []string) {
	// Reset visited flags
	for _, node := range d.nodes {
		node.Visited = false
		node.InStack = false
	}
	
	var cycle []string
	
	var visit func(node *Node, path []string) bool
	visit = func(node *Node, path []string) bool {
		node.Visited = true
		node.InStack = true
		path = append(path, node.Name)
		
		for _, dep := range node.Dependencies {
			if !dep.Visited {
				if visit(dep, path) {
					return true
				}
			} else if dep.InStack {
				// Found a cycle, build the cycle path
				startIdx := 0
				for i, name := range path {
					if name == dep.Name {
						startIdx = i
						break
					}
				}
				cycle = path[startIdx:]
				cycle = append(cycle, dep.Name) // Complete the cycle
				return true
			}
		}
		
		node.InStack = false
		return false
	}
	
	for _, node := range d.nodes {
		if !node.Visited {
			if visit(node, []string{}) {
				return true, cycle
			}
		}
	}
	
	return false, nil
}

func (d *DAG) TopologicalSort() ([]*Node, error) {
	// First check for cycles
	if hasCycle, cycle := d.DetectCycles(); hasCycle {
		return nil, fmt.Errorf("dependency cycle detected: %s", strings.Join(cycle, " -> "))
	}
	
	// Reset visited flags
	for _, node := range d.nodes {
		node.Visited = false
	}
	
	var sorted []*Node
	
	var visit func(node *Node)
	visit = func(node *Node) {
		if node.Visited {
			return
		}
		node.Visited = true
		
		// Visit all dependencies first
		for _, dep := range node.Dependencies {
			visit(dep)
		}
		
		// Add node after its dependencies
		sorted = append(sorted, node)
	}
	
	// Visit all nodes
	for _, node := range d.nodes {
		visit(node)
	}
	
	return sorted, nil
}

func (d *DAG) FindAffectedNodes(nodeName string) []*Node {
	node, exists := d.nodes[nodeName]
	if !exists {
		return []*Node{}
	}
	
	// Reset visited flags
	for _, n := range d.nodes {
		n.Visited = false
	}
	
	affected := make(map[string]*Node)
	
	var visit func(n *Node)
	visit = func(n *Node) {
		if n.Visited {
			return
		}
		n.Visited = true
		affected[n.Name] = n
		
		// Visit all dependents
		for _, dep := range n.Dependents {
			visit(dep)
		}
	}
	
	visit(node)
	
	// Convert to slice and sort
	var result []*Node
	for _, n := range affected {
		result = append(result, n)
	}
	
	sort.Slice(result, func(i, j int) bool {
		return result[i].Name < result[j].Name
	})
	
	return result
}

func (d *DAG) GetNode(name string) (*Node, bool) {
	node, exists := d.nodes[name]
	return node, exists
}

func (d *DAG) GetAllNodes() []*Node {
	var nodes []*Node
	for _, node := range d.nodes {
		nodes = append(nodes, node)
	}
	
	sort.Slice(nodes, func(i, j int) bool {
		return nodes[i].Name < nodes[j].Name
	})
	
	return nodes
}

func (d *DAG) GetRoots() []*Node {
	return d.roots
}

func (d *DAG) BuildFromIntents(intents []*IntentFile) error {
	// First pass: add all nodes
	for _, intent := range intents {
		d.AddNode(intent.Name, intent)
	}
	
	// Second pass: add edges
	for _, intent := range intents {
		for _, dep := range intent.Dependencies {
			if err := d.AddEdge(intent.Name, dep); err != nil {
				return fmt.Errorf("failed to add edge from %s to %s: %w", intent.Name, dep, err)
			}
		}
	}
	
	// Check for missing dependencies
	for _, node := range d.nodes {
		if node.Intent == nil && len(node.Dependents) > 0 {
			dependents := []string{}
			for _, dep := range node.Dependents {
				dependents = append(dependents, dep.Name)
			}
			return fmt.Errorf("missing intent file for %s (required by: %s)", 
				node.Name, strings.Join(dependents, ", "))
		}
	}
	
	return nil
}

func (d *DAG) Visualize() string {
	var builder strings.Builder
	
	builder.WriteString("Dependency Graph:\n")
	builder.WriteString("================\n\n")
	
	// Check for cycles first
	if hasCycle, cycle := d.DetectCycles(); hasCycle {
		builder.WriteString(fmt.Sprintf("Error: dependency cycle detected: %s\n", strings.Join(cycle, " -> ")))
		return builder.String()
	}
	
	// Build a tree visualization
	printed := make(map[string]bool)
	
	var printNode func(node *Node, prefix string, isLast bool)
	printNode = func(node *Node, prefix string, isLast bool) {
		if printed[node.Name] {
			return
		}
		printed[node.Name] = true
		
		// Print current node
		connector := "├── "
		if isLast {
			connector = "└── "
		}
		
		builder.WriteString(fmt.Sprintf("%s%s%s\n", prefix, connector, node.Name))
		
		// Prepare prefix for children
		childPrefix := prefix
		if isLast {
			childPrefix += "    "
		} else {
			childPrefix += "│   "
		}
		
		// Print dependents
		for i, dep := range node.Dependents {
			isLastDep := i == len(node.Dependents)-1
			printNode(dep, childPrefix, isLastDep)
		}
	}
	
	// Start with roots
	roots := d.GetRoots()
	if len(roots) == 0 {
		builder.WriteString("No root nodes found\n")
	} else {
		for i, root := range roots {
			if i > 0 {
				builder.WriteString("\n")
			}
			printNode(root, "", true)
		}
	}
	
	return builder.String()
}

func (d *DAG) VisualizeWithStatus(statusFunc func(string) string) string {
	var builder strings.Builder
	
	builder.WriteString("Dependency Graph with Status:\n")
	builder.WriteString("============================\n\n")
	
	// Get all nodes sorted
	nodes := d.GetAllNodes()
	
	// Build status legend
	builder.WriteString("Legend: [✓] built, [✗] failed, [○] pending, [◐] building, [!] outdated\n\n")
	
	printed := make(map[string]bool)
	
	var printNode func(node *Node, prefix string, isLast bool)
	printNode = func(node *Node, prefix string, isLast bool) {
		if printed[node.Name] {
			return
		}
		printed[node.Name] = true
		
		// Get status
		status := "○" // default pending
		if statusFunc != nil {
			switch statusFunc(node.Name) {
			case "built":
				status = "✓"
			case "failed":
				status = "✗"
			case "building":
				status = "◐"
			case "outdated":
				status = "!"
			}
		}
		
		// Print current node
		connector := "├── "
		if isLast {
			connector = "└── "
		}
		
		builder.WriteString(fmt.Sprintf("%s%s[%s] %s\n", prefix, connector, status, node.Name))
		
		// Prepare prefix for children
		childPrefix := prefix
		if isLast {
			childPrefix += "    "
		} else {
			childPrefix += "│   "
		}
		
		// Print dependents
		for i, dep := range node.Dependents {
			isLastDep := i == len(node.Dependents)-1
			printNode(dep, childPrefix, isLastDep)
		}
	}
	
	// Start with roots
	roots := d.GetRoots()
	if len(roots) == 0 {
		// If no roots, might be a cycle, show all nodes
		for i, node := range nodes {
			if !printed[node.Name] {
				if i > 0 {
					builder.WriteString("\n")
				}
				printNode(node, "", true)
			}
		}
	} else {
		for i, root := range roots {
			if i > 0 {
				builder.WriteString("\n")
			}
			printNode(root, "", true)
		}
	}
	
	return builder.String()
}