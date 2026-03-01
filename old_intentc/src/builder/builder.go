package builder

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/agent"
	"github.com/pboueri/intentc/src/config"
	"github.com/pboueri/intentc/src/git"
	"github.com/pboueri/intentc/src/logger"
	"github.com/pboueri/intentc/src/parser"
	"github.com/pboueri/intentc/src/state"
)

type Builder struct {
	projectRoot  string
	intentDir    string
	agent        agent.Agent
	stateManager state.StateManager
	parser       *parser.Parser
	gitManager   git.GitManager
	config       *config.Config
}

type BuildOptions struct {
	Target    string
	Force     bool
	DryRun    bool
	BuildName string // Name for the build directory (optional, uses default if empty)
}

func NewBuilder(projectRoot string, agent agent.Agent, stateManager state.StateManager, gitManager git.GitManager, cfg *config.Config) *Builder {
	return &Builder{
		projectRoot:  projectRoot,
		intentDir:    filepath.Join(projectRoot, "intent"),
		agent:        agent,
		stateManager: stateManager,
		parser:       parser.New(),
		gitManager:   gitManager,
		config:       cfg,
	}
}

func (b *Builder) Build(ctx context.Context, opts BuildOptions) error {
	// Determine build name
	buildName := opts.BuildName
	if buildName == "" {
		buildName = b.config.Build.DefaultBuildName
	}
	
	// Create build directory
	buildPath, err := b.createBuildDirectory(buildName)
	if err != nil {
		return fmt.Errorf("failed to create build directory: %w", err)
	}
	
	logger.Info("Using build directory: %s", buildPath)

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
		targetsToBuild = b.getAllUnbuiltTargets(ctx, dag, buildName)
	}

	if opts.DryRun {
		logger.Info("Targets to build:")
		for _, target := range targetsToBuild {
			logger.Info("  - %s", target.Name)
		}
		return nil
	}

	for _, target := range targetsToBuild {
		if err := b.buildTarget(ctx, target, opts.Force, buildName, buildPath); err != nil {
			return fmt.Errorf("failed to build target %s: %w", target.Name, err)
		}
	}

	return nil
}

func (b *Builder) buildTarget(ctx context.Context, target *src.Target, force bool, buildName, buildPath string) error {
	// Use build-aware status check
	status, err := b.stateManager.GetTargetStatusForBuild(ctx, target.Name, buildName)
	if err != nil {
		return fmt.Errorf("failed to get target status: %w", err)
	}

	if status == src.TargetStatusBuilt && !force {
		logger.Info("Target %s is already built in build '%s', skipping", target.Name, buildName)
		return nil
	}

	logger.Info("Building target: %s", target.Name)
	
	// Update build-specific status
	if err := b.stateManager.UpdateTargetStatusForBuild(ctx, target.Name, buildName, src.TargetStatusBuilding); err != nil {
		return fmt.Errorf("failed to update target status: %w", err)
	}
	// Also update global status for backward compatibility
	if err := b.stateManager.UpdateTargetStatus(ctx, target.Name, src.TargetStatusBuilding); err != nil {
		return fmt.Errorf("failed to update global target status: %w", err)
	}

	generationID := b.generateID()
	
	buildCtx := agent.BuildContext{
		Intent:       target.Intent,
		Validations:  target.Validations,
		ProjectRoot:  b.projectRoot,
		GenerationID: generationID,
		GitManager:   b.gitManager,
		BuildName:    buildName,
		BuildPath:    buildPath,
	}

	files, err := b.agent.Build(ctx, buildCtx)
	if err != nil {
		b.stateManager.UpdateTargetStatusForBuild(ctx, target.Name, buildName, src.TargetStatusFailed)
		b.stateManager.UpdateTargetStatus(ctx, target.Name, src.TargetStatusFailed)
		return fmt.Errorf("agent build failed: %w", err)
	}

	result := &src.BuildResult{
		Target:       target.Name,
		GenerationID: generationID,
		Success:      true,
		GeneratedAt:  time.Now(),
		Files:        files,
		BuildName:    buildName,
		BuildPath:    buildPath,
	}

	// Save build result - if GitStateManager, it will handle build-specific storage
	if err := b.stateManager.SaveBuildResult(ctx, result); err != nil {
		return fmt.Errorf("failed to save build result: %w", err)
	}

	// Update build-specific status
	if err := b.stateManager.UpdateTargetStatusForBuild(ctx, target.Name, buildName, src.TargetStatusBuilt); err != nil {
		return fmt.Errorf("failed to update target status: %w", err)
	}
	// Also update global status for backward compatibility
	if err := b.stateManager.UpdateTargetStatus(ctx, target.Name, src.TargetStatusBuilt); err != nil {
		return fmt.Errorf("failed to update global target status: %w", err)
	}

	logger.Info("Successfully built target: %s (generation ID: %s)", target.Name, generationID)
	return nil
}

func (b *Builder) loadTargets(ctx context.Context) (map[string]*src.Target, error) {
	targets := make(map[string]*src.Target)
	
	// Create target registry and load all targets
	targetRegistry := parser.NewTargetRegistry(b.projectRoot)
	if err := targetRegistry.LoadTargets(); err != nil {
		return nil, fmt.Errorf("failed to load targets: %w", err)
	}

	// Convert from registry format to builder format
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

func (b *Builder) getAllUnbuiltTargets(ctx context.Context, dag map[string]*src.Target, buildName string) []*src.Target {
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

		status, _ := b.stateManager.GetTargetStatusForBuild(ctx, t.Name, buildName)
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

// createBuildDirectory creates the build directory and returns its path
func (b *Builder) createBuildDirectory(buildName string) (string, error) {
	// Build directory path: {projectRoot}/build-{buildName}
	buildPath := filepath.Join(b.projectRoot, "build-"+buildName)
	
	// Create directory if it doesn't exist
	if err := os.MkdirAll(buildPath, 0755); err != nil {
		return "", fmt.Errorf("failed to create build directory: %w", err)
	}
	
	return buildPath, nil
}

// GetBuildDirectory returns the path to a specific build directory
func (b *Builder) GetBuildDirectory(buildName string) string {
	if buildName == "" {
		buildName = b.config.Build.DefaultBuildName
	}
	return filepath.Join(b.projectRoot, "build-"+buildName)
}