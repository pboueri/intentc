package cmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src/agent"
	"github.com/pboueri/intentc/src/builder"
	"github.com/pboueri/intentc/src/config"
	"github.com/pboueri/intentc/src/git"
	"github.com/pboueri/intentc/src/state"
)

var (
	buildForce  bool
	buildDryRun bool
	buildName   string
)

func init() {
	buildCmd.Flags().BoolVarP(&buildForce, "force", "f", false, "Force rebuild even if target is up to date")
	buildCmd.Flags().BoolVar(&buildDryRun, "dry-run", false, "Show what would be built without actually building")
	buildCmd.Flags().StringVar(&buildName, "build-name", "", "Name for the build directory (uses default if not specified)")
}

var buildCmd = &cobra.Command{
	Use:   "build [target]",
	Short: "Build targets from intents",
	Long:  `Build targets from intent files using AI agents. If no target is specified, builds all unbuilt targets.`,
	Args:  cobra.MaximumNArgs(1),
	RunE:  runBuild,
}

func runBuild(cmd *cobra.Command, args []string) error {
	projectRoot, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("failed to get current directory: %w", err)
	}

	// Check if .intentc exists
	configPath := filepath.Join(projectRoot, ".intentc")
	if _, err := os.Stat(configPath); os.IsNotExist(err) {
		return fmt.Errorf("not in an intentc project (no .intentc found). Run 'intentc init' first")
	}

	// Load configuration with overrides
	cfg, err := LoadConfigWithOverrides(projectRoot)
	if err != nil {
		return err
	}

	// Initialize logger with project config
	if err := config.InitializeLogger(cfg, projectRoot); err != nil {
		return fmt.Errorf("failed to initialize logger: %w", err)
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

	// Create agent based on configuration
	buildAgent, err := agent.CreateFromConfig(cfg, "default-agent")
	if err != nil {
		return fmt.Errorf("failed to create agent: %w", err)
	}

	// Create builder
	bldr := builder.NewBuilder(projectRoot, buildAgent, stateManager, gitInterface, cfg)

	// Build options
	opts := builder.BuildOptions{
		Force:     buildForce,
		DryRun:    buildDryRun,
		BuildName: buildName,
	}

	if len(args) > 0 {
		opts.Target = args[0]
	}

	// Run build
	if err := bldr.Build(context.Background(), opts); err != nil {
		return err
	}

	return nil
}
