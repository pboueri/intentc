package cmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src/agent"
	"github.com/pboueri/intentc/src/config"
	"github.com/pboueri/intentc/src/logger"
)

var (
	decompileOutput   string
	decompileCodePath string
)

func init() {
	decompileCmd.Flags().StringVarP(&decompileOutput, "output", "o", "", "Output directory for intents (defaults to current directory)")
	decompileCmd.Flags().StringVarP(&decompileCodePath, "code", "c", "", "Path to codebase to decompile (defaults to current directory)")
}

var decompileCmd = &cobra.Command{
	Use:   "decompile",
	Short: "Decompile a codebase into intent files",
	Long: `Decompile analyzes an existing codebase and generates intent files (.ic) that describe
the features and targets abstractly. This uses an AI agent to understand the high-level
purpose and structure of the code.`,
	RunE: runDecompile,
}

func runDecompile(cmd *cobra.Command, args []string) error {
	ctx := context.Background()

	// Get paths
	projectRoot, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("failed to get current directory: %w", err)
	}

	// Set code path (defaults to current directory)
	codePath := decompileCodePath
	if codePath == "" {
		codePath = projectRoot
	}
	codePath, err = filepath.Abs(codePath)
	if err != nil {
		return fmt.Errorf("failed to get absolute path for code: %w", err)
	}

	// Set output path (defaults to current directory)
	outputPath := decompileOutput
	if outputPath == "" {
		outputPath = projectRoot
	}
	outputPath, err = filepath.Abs(outputPath)
	if err != nil {
		return fmt.Errorf("failed to get absolute path for output: %w", err)
	}

	logger.Info("Decompiling codebase from %s to %s", codePath, outputPath)

	// Check if code path exists
	if _, err := os.Stat(codePath); os.IsNotExist(err) {
		return fmt.Errorf("code path does not exist: %s", codePath)
	}

	// Create intent directory in output path if it doesn't exist
	intentDir := filepath.Join(outputPath, "intent")
	if err := os.MkdirAll(intentDir, 0755); err != nil {
		return fmt.Errorf("failed to create intent directory: %w", err)
	}

	// Load configuration with overrides
	cfg, err := LoadConfigWithOverrides(projectRoot)
	if err != nil {
		// Use default config if no config found
		cfg = config.GetDefaultConfig()
		logger.Debug("Using default configuration")
	}

	// Create agent
	agentInstance, err := createAgentForDecompile(cfg, codePath)
	if err != nil {
		return fmt.Errorf("failed to create agent: %w", err)
	}

	// Create decompile context
	decompileCtx := agent.DecompileContext{
		SourcePath:  codePath,
		OutputPath:  intentDir,
		ProjectRoot: projectRoot,
	}

	// Execute decompile
	generatedFiles, err := agentInstance.Decompile(ctx, decompileCtx)
	if err != nil {
		return fmt.Errorf("decompile failed: %w", err)
	}

	if len(generatedFiles) == 0 {
		logger.Warn("No files were generated. The agent may need to be run with different settings or the codebase may be empty.")
	} else {
		logger.Info("Decompile completed successfully. Generated %d file(s):", len(generatedFiles))
		for _, file := range generatedFiles {
			relPath, _ := filepath.Rel(outputPath, file)
			if relPath == "" {
				relPath = file
			}
			logger.Info("  - %s", relPath)
		}
	}

	return nil
}

func createAgentForDecompile(cfg *config.Config, codePath string) (agent.Agent, error) {
	// Create agent with specific working directory for decompile
	return agent.CreateFromConfigWithWorkingDir(cfg, "decompile-agent", codePath)
}

