package builder

import (
	"context"
	"fmt"
	"path/filepath"
	"time"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/agent"
	"github.com/pboueri/intentc/src/parser"
	"github.com/pboueri/intentc/src/state"
)

type Builder struct {
	projectRoot  string
	intentDir    string
	agent        agent.Agent
	stateManager state.StateManager
	parser       *parser.Parser
}

type BuildOptions struct {
	Target    string
	Force     bool
	DryRun    bool
}

func NewBuilder(projectRoot string, agent agent.Agent, stateManager state.StateManager) *Builder {
	return &Builder{
		projectRoot:  projectRoot,
		intentDir:    filepath.Join(projectRoot, "intent"),
		agent:        agent,
		stateManager: stateManager,
		parser:       parser.New(),
	}
}

func (b *Builder) Build(ctx context.Context, opts BuildOptions) error {
	targets, err := b.loadTargets(ctx)
	if err != nil {
		return fmt.Errorf("failed to load targets: %w", err)
	}

	dag, err := b.buildDependencyGraph(targets)
	if err != nil {
		return fmt.Errorf("failed to build dependency graph: %w", err)
	}

	var targetsToBuild []*src.Target
	if opts.Target != "" {
		target, exists := targets[opts.Target]
		if !exists {
			return fmt.Errorf("target %s not found", opts.Target)
		}
		targetsToBuild = b.getTargetsInOrder(dag, target)
	} else {
		targetsToBuild = b.getAllUnbuiltTargets(ctx, dag)
	}

	if opts.DryRun {
		fmt.Println("Targets to build:")
		for _, target := range targetsToBuild {
			fmt.Printf("  - %s\n", target.Name)
		}
		return nil
	}

	for _, target := range targetsToBuild {
		if err := b.buildTarget(ctx, target, opts.Force); err != nil {
			return fmt.Errorf("failed to build target %s: %w", target.Name, err)
		}
	}

	return nil
}

func (b *Builder) buildTarget(ctx context.Context, target *src.Target, force bool) error {
	status, err := b.stateManager.GetTargetStatus(ctx, target.Name)
	if err != nil {
		return fmt.Errorf("failed to get target status: %w", err)
	}

	if status == src.TargetStatusBuilt && !force {
		fmt.Printf("Target %s is already built, skipping\n", target.Name)
		return nil
	}

	fmt.Printf("Building target: %s\n", target.Name)
	
	if err := b.stateManager.UpdateTargetStatus(ctx, target.Name, src.TargetStatusBuilding); err != nil {
		return fmt.Errorf("failed to update target status: %w", err)
	}

	generationID := b.generateID()
	
	buildCtx := agent.BuildContext{
		Intent:       target.Intent,
		Validations:  target.Validations,
		ProjectRoot:  b.projectRoot,
		GenerationID: generationID,
	}

	files, err := b.agent.Build(ctx, buildCtx)
	if err != nil {
		b.stateManager.UpdateTargetStatus(ctx, target.Name, src.TargetStatusFailed)
		return fmt.Errorf("agent build failed: %w", err)
	}

	result := &src.BuildResult{
		Target:       target.Name,
		GenerationID: generationID,
		Success:      true,
		GeneratedAt:  time.Now(),
		Files:        files,
	}

	if err := b.stateManager.SaveBuildResult(ctx, result); err != nil {
		return fmt.Errorf("failed to save build result: %w", err)
	}

	if err := b.stateManager.UpdateTargetStatus(ctx, target.Name, src.TargetStatusBuilt); err != nil {
		return fmt.Errorf("failed to update target status: %w", err)
	}

	fmt.Printf("Successfully built target: %s (generation ID: %s)\n", target.Name, generationID)
	return nil
}

func (b *Builder) loadTargets(ctx context.Context) (map[string]*src.Target, error) {
	targets := make(map[string]*src.Target)
	
	features, err := b.parser.ParseIntentDirectory(b.intentDir)
	if err != nil {
		return nil, fmt.Errorf("failed to parse intent directory: %w", err)
	}

	for _, feature := range features {
		intent, err := b.parser.ParseIntentFile(filepath.Join(b.intentDir, feature, feature+".ic"))
		if err != nil {
			return nil, fmt.Errorf("failed to parse intent file for %s: %w", feature, err)
		}

		validations, err := b.parser.ParseValidationFiles(filepath.Join(b.intentDir, feature))
		if err != nil {
			return nil, fmt.Errorf("failed to parse validation files for %s: %w", feature, err)
		}

		target := &src.Target{
			Name:        feature,
			Intent:      intent,
			Validations: validations,
		}
		targets[feature] = target
	}

	return targets, nil
}

func (b *Builder) buildDependencyGraph(targets map[string]*src.Target) (map[string]*src.Target, error) {
	for name, target := range targets {
		for _, depName := range target.Intent.Dependencies {
			dep, exists := targets[depName]
			if !exists {
				return nil, fmt.Errorf("target %s depends on unknown target %s", name, depName)
			}
			target.Dependencies = append(target.Dependencies, dep)
		}
	}

	if err := b.checkForCycles(targets); err != nil {
		return nil, err
	}

	return targets, nil
}

func (b *Builder) checkForCycles(targets map[string]*src.Target) error {
	visited := make(map[string]bool)
	recStack := make(map[string]bool)

	var hasCycle func(target *src.Target) bool
	hasCycle = func(target *src.Target) bool {
		visited[target.Name] = true
		recStack[target.Name] = true

		for _, dep := range target.Dependencies {
			if !visited[dep.Name] {
				if hasCycle(dep) {
					return true
				}
			} else if recStack[dep.Name] {
				return true
			}
		}

		recStack[target.Name] = false
		return false
	}

	for _, target := range targets {
		if !visited[target.Name] {
			if hasCycle(target) {
				return fmt.Errorf("dependency cycle detected")
			}
		}
	}

	return nil
}

func (b *Builder) getTargetsInOrder(dag map[string]*src.Target, target *src.Target) []*src.Target {
	visited := make(map[string]bool)
	var order []*src.Target

	var visit func(t *src.Target)
	visit = func(t *src.Target) {
		if visited[t.Name] {
			return
		}
		visited[t.Name] = true

		for _, dep := range t.Dependencies {
			visit(dep)
		}

		order = append(order, t)
	}

	visit(target)
	return order
}

func (b *Builder) getAllUnbuiltTargets(ctx context.Context, dag map[string]*src.Target) []*src.Target {
	var unbuilt []*src.Target
	visited := make(map[string]bool)

	var visit func(t *src.Target)
	visit = func(t *src.Target) {
		if visited[t.Name] {
			return
		}
		visited[t.Name] = true

		for _, dep := range t.Dependencies {
			visit(dep)
		}

		status, _ := b.stateManager.GetTargetStatus(ctx, t.Name)
		if status != src.TargetStatusBuilt {
			unbuilt = append(unbuilt, t)
		}
	}

	for _, target := range dag {
		visit(target)
	}

	return unbuilt
}

func (b *Builder) generateID() string {
	return fmt.Sprintf("gen-%d", time.Now().Unix())
}