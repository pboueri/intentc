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
)

func init() {
	buildCmd.Flags().BoolVarP(&buildForce, "force", "f", false, "Force rebuild even if target is up to date")
	buildCmd.Flags().BoolVar(&buildDryRun, "dry-run", false, "Show what would be built without actually building")
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
	var buildAgent agent.Agent
	switch cfg.Agent.Provider {
	case "claude":
		// For backwards compatibility, claude provider uses the ClaudeAgent
		claudeConfig := agent.ClaudeAgentConfig{
			Timeout:   cfg.Agent.Timeout,
			Retries:   cfg.Agent.Retries,
			RateLimit: cfg.Agent.RateLimit,
			CLIArgs:   cfg.Agent.CLIArgs,
		}
		buildAgent = agent.NewClaudeAgent("default-agent", claudeConfig)
	case "cli":
		// Generic CLI agent
		if cfg.Agent.Command == "" {
			return fmt.Errorf("CLI agent requires 'command' to be specified in config")
		}
		cliConfig := agent.CLIAgentConfig{
			Name:      "default-agent",
			Command:   cfg.Agent.Command,
			Args:      cfg.Agent.CLIArgs,
			Timeout:   cfg.Agent.Timeout,
			Retries:   cfg.Agent.Retries,
			RateLimit: cfg.Agent.RateLimit,
		}
		buildAgent = agent.NewCLIAgent(cliConfig)
	case "mock":
		buildAgent = agent.NewMockAgent("default-agent")
	default:
		// Check if command is specified for custom CLI agent
		if cfg.Agent.Command != "" {
			cliConfig := agent.CLIAgentConfig{
				Name:      "default-agent",
				Command:   cfg.Agent.Command,
				Args:      cfg.Agent.CLIArgs,
				Timeout:   cfg.Agent.Timeout,
				Retries:   cfg.Agent.Retries,
				RateLimit: cfg.Agent.RateLimit,
			}
			buildAgent = agent.NewCLIAgent(cliConfig)
		} else {
			return fmt.Errorf("unknown agent provider: %s", cfg.Agent.Provider)
		}
	}

	// Create builder
	bldr := builder.NewBuilder(projectRoot, buildAgent, stateManager, gitInterface)

	// Build options
	opts := builder.BuildOptions{
		Force:  buildForce,
		DryRun: buildDryRun,
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
