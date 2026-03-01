package cmd

import (
	"context"
	"fmt"
	"os"

	"github.com/pboueri/intentc/src/config"
	"github.com/pboueri/intentc/src/logger"
	"github.com/pboueri/intentc/src/repl"
	"github.com/spf13/cobra"
)

var refineCmd = &cobra.Command{
	Use:   "refine [target]",
	Short: "Enter refinement REPL",
	Long: `Enter an interactive REPL to refine generated code iteratively.

The refinement REPL allows you to:
  - View generated code
  - Request changes from the AI agent
  - Run validations
  - Commit refined code
  - Track refinement history

Example:
  intentc refine api      # Refine the 'api' target
  intentc refine         # Refine the default target (if only one exists)`,
	RunE: runRefine,
}

func runRefine(cmd *cobra.Command, args []string) error {
	ctx := context.Background()

	// Get project root
	projectRoot, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("failed to get current directory: %w", err)
	}

	// Load configuration
	cfg, err := config.LoadConfig(projectRoot)
	if err != nil {
		return fmt.Errorf("failed to load config: %w", err)
	}

	// Determine target
	var target string
	if len(args) > 0 {
		target = args[0]
	} else {
		// If no target specified, try to find a default
		// This is a simplified approach - could be enhanced
		logger.Warn("No target specified. Please specify a target to refine.")
		fmt.Fprintln(cmd.OutOrStderr(), "Usage: intentc refine <target>")
		return fmt.Errorf("target required")
	}

	// Create and run REPL
	r, err := repl.NewREPL(cfg, target)
	if err != nil {
		return fmt.Errorf("failed to create REPL: %w", err)
	}

	logger.Info("Starting refinement REPL for target: %s", target)
	return r.Run(ctx)
}
