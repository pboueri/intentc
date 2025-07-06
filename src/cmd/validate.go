package cmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/agent"
	"github.com/pboueri/intentc/src/git"
	"github.com/pboueri/intentc/src/parser"
	"github.com/pboueri/intentc/src/state"
	"github.com/pboueri/intentc/src/validation"
)

var (
	validateParallel bool
	validateTimeout  int
)

func init() {
	validateCmd.Flags().BoolVarP(&validateParallel, "parallel", "p", false, "Run validations in parallel")
	validateCmd.Flags().IntVar(&validateTimeout, "timeout", 30, "Timeout for each validation in seconds")
}

var validateCmd = &cobra.Command{
	Use:   "validate [target]",
	Short: "Run validations",
	Long:  `Run validations for a target and generate a report of what passed or failed.`,
	Args:  cobra.MaximumNArgs(1),
	RunE:  runValidate,
}

func runValidate(cmd *cobra.Command, args []string) error {
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

	// Create agent (for now, use mock agent)
	mockAgent := agent.NewMockAgent("default-agent")

	// Create validator registry and register built-in validators
	registry := validation.NewValidatorRegistry()
	validation.RegisterBuiltinValidators(registry, mockAgent)

	// Create validation runner
	runner := validation.NewRunner(projectRoot, registry)

	// Parse targets
	p := parser.New()
	intentDir := filepath.Join(projectRoot, "intent")
	
	if len(args) == 0 {
		return fmt.Errorf("target name required")
	}

	targetName := args[0]
	
	// Load target
	intent, err := p.ParseIntentFile(filepath.Join(intentDir, targetName, targetName+".ic"))
	if err != nil {
		return fmt.Errorf("failed to parse intent file: %w", err)
	}

	validations, err := p.ParseValidationFiles(filepath.Join(intentDir, targetName))
	if err != nil {
		return fmt.Errorf("failed to parse validation files: %w", err)
	}

	target := &src.Target{
		Name:        targetName,
		Intent:      intent,
		Validations: validations,
	}

	// Check if target has been built
	status, err := stateManager.GetTargetStatus(context.Background(), targetName)
	if err != nil {
		return fmt.Errorf("failed to get target status: %w", err)
	}

	if status != src.TargetStatusBuilt {
		fmt.Printf("Warning: Target %s has not been built yet. Validation results may be incomplete.\n\n", targetName)
	}

	// Run validations
	opts := validation.RunOptions{
		Parallel: validateParallel,
		Timeout:  time.Duration(validateTimeout) * time.Second,
	}

	result, err := runner.RunTargetValidations(context.Background(), target, opts)
	if err != nil {
		return fmt.Errorf("failed to run validations: %w", err)
	}

	// Generate and print report
	report := runner.GenerateReport(result)
	fmt.Println(report)

	// Return error if any validations failed
	if result.Failed > 0 {
		return fmt.Errorf("%d validation(s) failed", result.Failed)
	}

	return nil
}
