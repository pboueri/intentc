package cleaner

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/parser"
	"github.com/pboueri/intentc/src/state"
)

type Cleaner struct {
	projectRoot  string
	intentDir    string
	stateManager state.StateManager
	parser       *parser.Parser
}

type CleanOptions struct {
	Target    string
	DryRun    bool
	BuildName string // Optional: specific build directory to clean
}

func NewCleaner(projectRoot string, stateManager state.StateManager) *Cleaner {
	return &Cleaner{
		projectRoot:  projectRoot,
		intentDir:    filepath.Join(projectRoot, "intent"),
		stateManager: stateManager,
		parser:       parser.New(),
	}
}

func (c *Cleaner) Clean(ctx context.Context, opts CleanOptions) error {
	// If build name is specified, clean the entire build directory
	if opts.BuildName != "" {
		return c.cleanBuildDirectory(opts.BuildName, opts.DryRun)
	}

	// Otherwise, clean based on targets (legacy behavior for now)
	// In the future, this could be updated to clean from the default build directory
	targets, err := c.loadTargets(ctx)
	if err != nil {
		return fmt.Errorf("failed to load targets: %w", err)
	}

	dag, err := c.buildDependencyGraph(targets)
	if err != nil {
		return fmt.Errorf("failed to build dependency graph: %w", err)
	}

	var targetsToClean []*src.Target
	if opts.Target != "" {
		target, exists := targets[opts.Target]
		if !exists {
			return fmt.Errorf("target %s not found", opts.Target)
		}
		targetsToClean = c.getTargetsAndDependents(dag, target)
		// Debug output
		// fmt.Printf("Debug: getTargetsAndDependents returned %d targets\n", len(targetsToClean))
		// for _, t := range targetsToClean {
		// 	fmt.Printf("  - %s\n", t.Name)
		// }
	} else {
		// Clean all targets
		for _, target := range targets {
			targetsToClean = append(targetsToClean, target)
		}
	}

	if opts.DryRun {
		fmt.Println("Targets to clean:")
		for _, target := range targetsToClean {
			fmt.Printf("  - %s\n", target.Name)
		}
		return nil
	}

	for _, target := range targetsToClean {
		if err := c.cleanTarget(ctx, target); err != nil {
			return fmt.Errorf("failed to clean target %s: %w", target.Name, err)
		}
	}

	return nil
}

func (c *Cleaner) cleanTarget(ctx context.Context, target *src.Target) error {
	fmt.Printf("Cleaning target: %s\n", target.Name)

	// Get the latest build result to find generated files
	result, err := c.stateManager.GetLatestBuildResult(ctx, target.Name)
	if err != nil {
		return fmt.Errorf("failed to get build result: %w", err)
	}

	if result == nil {
		fmt.Printf("Target %s has no build results, skipping\n", target.Name)
		return nil
	}

	// Remove generated files
	for _, file := range result.Files {
		// Use build path if available, otherwise default to project root
		var filePath string
		if result.BuildPath != "" {
			filePath = filepath.Join(result.BuildPath, file)
		} else {
			filePath = filepath.Join(c.projectRoot, file)
		}
		
		if err := os.Remove(filePath); err != nil {
			if !os.IsNotExist(err) {
				fmt.Printf("Warning: failed to remove %s: %v\n", filePath, err)
			} else {
				fmt.Printf("File not found (already removed?): %s\n", filePath)
			}
		} else {
			fmt.Printf("Removed: %s\n", file)
		}
	}

	// Update target status
	// If the result has a build name, update the build-specific status
	if result.BuildName != "" {
		if err := c.stateManager.UpdateTargetStatusForBuild(ctx, target.Name, result.BuildName, src.TargetStatusPending); err != nil {
			return fmt.Errorf("failed to update target status: %w", err)
		}
	}
	// Also update global status for backward compatibility
	if err := c.stateManager.UpdateTargetStatus(ctx, target.Name, src.TargetStatusPending); err != nil {
		return fmt.Errorf("failed to update target status: %w", err)
	}

	fmt.Printf("Successfully cleaned target: %s\n", target.Name)
	return nil
}

func (c *Cleaner) loadTargets(ctx context.Context) (map[string]*src.Target, error) {
	targets := make(map[string]*src.Target)
	
	// Create target registry and load all targets
	targetRegistry := parser.NewTargetRegistry(c.projectRoot)
	if err := targetRegistry.LoadTargets(); err != nil {
		return nil, fmt.Errorf("failed to load targets: %w", err)
	}

	// Convert from registry format to cleaner format
	for _, targetInfo := range targetRegistry.GetAllTargets() {
		if targetInfo.Intent == nil {
			continue
		}

		target := &src.Target{
			Name:        targetInfo.Name,
			Intent:      targetInfo.Intent,
			Validations: targetInfo.ValidationFiles,
		}
		targets[targetInfo.Name] = target
	}

	return targets, nil
}

func (c *Cleaner) buildDependencyGraph(targets map[string]*src.Target) (map[string]*src.Target, error) {
	for name, target := range targets {
		// Debug output
		fmt.Printf("DEBUG: Target %s has dependencies: %v\n", name, target.Intent.Dependencies)
		
		for _, depName := range target.Intent.Dependencies {
			dep, exists := targets[depName]
			if !exists {
				// More detailed error message
				fmt.Printf("DEBUG: Available targets: %v\n", getTargetNames(targets))
				return nil, fmt.Errorf("target %s depends on unknown target %s", name, depName)
			}
			target.Dependencies = append(target.Dependencies, dep)
		}
	}

	return targets, nil
}

func getTargetNames(targets map[string]*src.Target) []string {
	names := make([]string, 0, len(targets))
	for name := range targets {
		names = append(names, name)
	}
	return names
}

func (c *Cleaner) getTargetsAndDependents(dag map[string]*src.Target, target *src.Target) []*src.Target {
	// Build reverse dependency map
	dependents := make(map[string][]*src.Target)
	for _, t := range dag {
		for _, dep := range t.Dependencies {
			dependents[dep.Name] = append(dependents[dep.Name], t)
		}
	}
	
	// Debug: print dependency map
	// fmt.Printf("Debug: Dependents map for target %s:\n", target.Name)
	// for k, v := range dependents {
	// 	fmt.Printf("  %s has dependents: ", k)
	// 	for _, d := range v {
	// 		fmt.Printf("%s ", d.Name)
	// 	}
	// 	fmt.Println()
	// }

	// Collect all targets that depend on the given target
	visited := make(map[string]bool)
	var result []*src.Target

	var visit func(t *src.Target)
	visit = func(t *src.Target) {
		if visited[t.Name] {
			return
		}
		visited[t.Name] = true
		result = append(result, t)

		// Visit all targets that depend on this one
		for _, dependent := range dependents[t.Name] {
			visit(dependent)
		}
	}

	visit(target)
	return result
}

// cleanBuildDirectory removes an entire build directory
func (c *Cleaner) cleanBuildDirectory(buildName string, dryRun bool) error {
	buildPath := filepath.Join(c.projectRoot, "build-"+buildName)
	
	// Check if directory exists
	if _, err := os.Stat(buildPath); os.IsNotExist(err) {
		fmt.Printf("Build directory '%s' does not exist\n", buildName)
		return nil
	}
	
	if dryRun {
		fmt.Printf("Would remove build directory: %s\n", buildPath)
		return nil
	}
	
	// Remove the directory and all its contents
	if err := os.RemoveAll(buildPath); err != nil {
		return fmt.Errorf("failed to remove build directory: %w", err)
	}
	
	fmt.Printf("Removed build directory: %s\n", buildPath)
	return nil
}