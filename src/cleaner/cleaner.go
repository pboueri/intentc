package cleaner

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/intent"
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
	Target string
	DryRun bool
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
		filePath := filepath.Join(c.projectRoot, file)
		if err := os.Remove(filePath); err != nil && !os.IsNotExist(err) {
			fmt.Printf("Warning: failed to remove %s: %v\n", file, err)
		} else {
			fmt.Printf("Removed: %s\n", file)
		}
	}

	// Update target status
	if err := c.stateManager.UpdateTargetStatus(ctx, target.Name, src.TargetStatusPending); err != nil {
		return fmt.Errorf("failed to update target status: %w", err)
	}

	fmt.Printf("Successfully cleaned target: %s\n", target.Name)
	return nil
}

func (c *Cleaner) loadTargets(ctx context.Context) (map[string]*src.Target, error) {
	targets := make(map[string]*src.Target)
	
	// Create target registry and load all targets
	targetRegistry := intent.NewTargetRegistry(c.projectRoot)
	if err := targetRegistry.LoadTargets(); err != nil {
		return nil, fmt.Errorf("failed to load targets: %w", err)
	}

	// Convert from registry format to cleaner format
	for _, targetInfo := range targetRegistry.GetAllTargets() {
		if targetInfo.Intent == nil {
			continue
		}

		// Convert IntentFile to Intent
		intentData := &src.Intent{
			Name:         targetInfo.Intent.Name,
			Dependencies: targetInfo.Intent.Dependencies,
			Content:      targetInfo.Intent.RawContent,
			FilePath:     targetInfo.Intent.Path,
		}

		// Parse validation files
		var validations []*src.ValidationFile
		for _, valFilePath := range targetInfo.ValidationFiles {
			valFile, err := c.parser.ParseValidationFile(valFilePath)
			if err != nil {
				return nil, fmt.Errorf("failed to parse validation file %s: %w", valFilePath, err)
			}
			validations = append(validations, valFile)
		}

		target := &src.Target{
			Name:        targetInfo.Name,
			Intent:      intentData,
			Validations: validations,
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