package cmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src/cleaner"
	"github.com/pboueri/intentc/src/git"
	"github.com/pboueri/intentc/src/state"
)

var cleanDryRun bool

func init() {
	cleanCmd.Flags().BoolVar(&cleanDryRun, "dry-run", false, "Show what would be cleaned without actually cleaning")
}

var cleanCmd = &cobra.Command{
	Use:   "clean [target]",
	Short: "Clean generated files",
	Long:  `Clean generated files from a target. If no target is specified, cleans all generated files.`,
	Args:  cobra.MaximumNArgs(1),
	RunE:  runClean,
}

func runClean(cmd *cobra.Command, args []string) error {
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

	// Create cleaner
	clnr := cleaner.NewCleaner(projectRoot, stateManager)

	// Clean options
	opts := cleaner.CleanOptions{
		DryRun: cleanDryRun,
	}

	if len(args) > 0 {
		opts.Target = args[0]
	}

	// Run clean
	if err := clnr.Clean(context.Background(), opts); err != nil {
		return err
	}

	return nil
}
