package cmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src/git"
	"github.com/pboueri/intentc/src/intent"
	"github.com/pboueri/intentc/src/logger"
	"github.com/pboueri/intentc/src/state"
)

var (
	statusTree bool
)

func init() {
	statusCmd.Flags().BoolVarP(&statusTree, "tree", "t", true, "Show dependency tree visualization")
}

var statusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show target status",
	Long:  `Show the current status of all targets, including what is out of date and when things were generated.`,
	RunE:  runStatus,
}

func runStatus(cmd *cobra.Command, args []string) error {
	projectRoot, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("failed to get current directory: %w", err)
	}

	// Check if .intentc exists
	configPath := filepath.Join(projectRoot, ".intentc")
	if _, err := os.Stat(configPath); os.IsNotExist(err) {
		return fmt.Errorf("not in an intentc project (no .intentc found). Run 'intentc init' first")
	}

	// Initialize git interface
	gitInterface := git.New()
	if err := gitInterface.Initialize(context.Background(), projectRoot); err != nil {
		return fmt.Errorf("failed to initialize git: %w", err)
	}

	// Initialize state manager
	stateManager := state.NewGitStateManager(gitInterface, projectRoot)
	if err := stateManager.Initialize(context.Background()); err != nil {
		return fmt.Errorf("failed to initialize state manager: %w", err)
	}

	// Create registry and load targets
	registry := intent.NewTargetRegistry(projectRoot)
	if err := registry.LoadTargets(); err != nil {
		return fmt.Errorf("failed to load targets: %w", err)
	}

	// Build dependency graph
	dag := intent.NewDAG()
	var intents []*intent.IntentFile
	for _, target := range registry.GetAllTargets() {
		if target.Intent != nil {
			intents = append(intents, target.Intent)
		}
	}

	if err := dag.BuildFromIntents(intents); err != nil {
		logger.Warn("Warning: %v", err)
	}

	// Print git status
	gitStatus, err := gitInterface.GetStatus(context.Background())
	if err == nil {
		fmt.Printf("Git Branch: %s\n", gitStatus.Branch)
		if !gitStatus.Clean {
			fmt.Printf("Working tree: \033[33mUncommitted changes\033[0m\n")
			if len(gitStatus.ModifiedFiles) > 0 {
				logger.Info("  Modified: %s", strings.Join(gitStatus.ModifiedFiles, ", "))
			}
			if len(gitStatus.UntrackedFiles) > 0 {
				logger.Info("  Untracked: %s", strings.Join(gitStatus.UntrackedFiles, ", "))
			}
		} else {
			fmt.Printf("Working tree: \033[32mClean\033[0m\n")
		}
		fmt.Println()
	}

	// Status function for DAG visualization
	statusFunc := func(name string) string {
		status, err := stateManager.GetTargetStatus(context.Background(), name)
		if err != nil {
			return "pending"
		}
		return string(status)
	}

	// Print dependency tree if requested
	if statusTree {
		fmt.Println(dag.VisualizeWithStatus(statusFunc))
		fmt.Println()
	}

	// Print detailed target status
	fmt.Println("Target Status:")
	fmt.Println("=============")
	
	targets := registry.GetAllTargets()
	for _, target := range targets {
		printTargetStatus(context.Background(), target, stateManager)
	}

	// Print summary
	var pending, built, failed, outdated int
	for _, target := range targets {
		status, _ := stateManager.GetTargetStatus(context.Background(), target.Name)
		switch string(status) {
		case "built":
			built++
		case "failed":
			failed++
		case "outdated":
			outdated++
		default:
			pending++
		}
	}

	fmt.Printf("\nSummary: %d targets total - %d built, %d pending, %d failed, %d outdated\n",
		len(targets), built, pending, failed, outdated)

	return nil
}

func printTargetStatus(ctx context.Context, target *intent.TargetInfo, stateManager state.StateManager) {
	status, err := stateManager.GetTargetStatus(ctx, target.Name)
	if err != nil {
		status = "pending"
	}

	// Get latest build result
	result, _ := stateManager.GetLatestBuildResult(ctx, target.Name)

	// Format status with color
	var statusStr string
	switch string(status) {
	case "built":
		statusStr = "\033[32m✓ built\033[0m"
	case "failed":
		statusStr = "\033[31m✗ failed\033[0m"
	case "building":
		statusStr = "\033[33m◐ building\033[0m"
	case "outdated":
		statusStr = "\033[33m! outdated\033[0m"
	default:
		statusStr = "\033[90m○ pending\033[0m"
	}

	fmt.Printf("\n%s: %s\n", target.Name, statusStr)

	if target.Intent != nil {
		logger.Info("  Intent: %s", target.Intent.Path)
		if len(target.Intent.Dependencies) > 0 {
			logger.Info("  Dependencies: %s", strings.Join(target.Intent.Dependencies, ", "))
		}
	}

	if result != nil {
		logger.Info("  Generation ID: %s", result.GenerationID)
		logger.Info("  Generated at: %s", result.GeneratedAt.Format(time.RFC3339))
		if len(result.Files) > 0 {
			logger.Info("  Generated files: %d", len(result.Files))
			for _, file := range result.Files {
				logger.Debug("    - %s", file)
			}
		}
	}

	if len(target.ValidationFiles) > 0 {
		logger.Info("  Validation files: %d", len(target.ValidationFiles))
	}
}
