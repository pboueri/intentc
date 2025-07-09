package cmd

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"path/filepath"
	"text/template"
	"time"

	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src"
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
		cfg = &config.Config{
			Version: 1,
			Agent: config.AgentConfig{
				Provider:  "claude",
				Timeout:   5 * time.Minute,
				Retries:   3,
				RateLimit: 1 * time.Second,
				CLIArgs:   []string{},
			},
			Build: config.BuildConfig{
				Parallel:     false,
				CacheEnabled: false,
			},
			Logging: config.LoggingConfig{
				Level: "info",
			},
		}
		logger.Debug("Using default configuration")
	}

	// Create agent
	agentInstance, err := createAgentForDecompile(cfg, codePath)
	if err != nil {
		return fmt.Errorf("failed to create agent: %w", err)
	}

	// Create the decompile prompt using template
	decompilePrompt, err := createDecompilePromptFromTemplate(codePath, intentDir)
	if err != nil {
		return fmt.Errorf("failed to create decompile prompt: %w", err)
	}

	// Execute the decompile using the agent's Build method
	buildCtx := agent.BuildContext{
		Intent: &src.Intent{
			Name:    "decompile",
			Content: decompilePrompt,
		},
		ProjectRoot:  intentDir, // Use intent directory as output location
		GenerationID: "decompile",
	}

	// Get list of files before running the agent
	filesBefore := make(map[string]bool)
	err = filepath.Walk(intentDir, func(path string, info os.FileInfo, err error) error {
		if err == nil && !info.IsDir() {
			filesBefore[path] = true
		}
		return nil
	})
	if err != nil {
		logger.Warn("Failed to scan directory before decompile: %v", err)
	}

	generatedFiles, err := agentInstance.Build(ctx, buildCtx)
	if err != nil {
		return fmt.Errorf("decompile failed: %w", err)
	}

	// If no files detected from agent, scan directory for new files
	if len(generatedFiles) == 0 {
		var newFiles []string
		err = filepath.Walk(intentDir, func(path string, info os.FileInfo, err error) error {
			if err == nil && !info.IsDir() && !filesBefore[path] {
				newFiles = append(newFiles, path)
			}
			return nil
		})
		if err != nil {
			logger.Warn("Failed to scan directory after decompile: %v", err)
		} else if len(newFiles) > 0 {
			generatedFiles = newFiles
		}
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
	// Create agent based on configuration (similar to build command)
	// But set the working directory to the code path for analysis
	switch cfg.Agent.Provider {
	case "claude":
		// Use CLI agent directly for decompile to avoid template system
		cliConfig := agent.CLIAgentConfig{
			Name:       "decompile-agent",
			Command:    "claude",
			Args:       cfg.Agent.CLIArgs,
			Timeout:    cfg.Agent.Timeout,
			Retries:    cfg.Agent.Retries,
			RateLimit:  cfg.Agent.RateLimit,
			WorkingDir: codePath, // Set working dir to source code location
		}
		return agent.NewCLIAgent(cliConfig), nil
	case "cli":
		// Generic CLI agent
		if cfg.Agent.Command == "" {
			return nil, fmt.Errorf("CLI agent requires 'command' to be specified in config")
		}
		cliConfig := agent.CLIAgentConfig{
			Name:       "decompile-agent",
			Command:    cfg.Agent.Command,
			Args:       cfg.Agent.CLIArgs,
			Timeout:    cfg.Agent.Timeout,
			Retries:    cfg.Agent.Retries,
			RateLimit:  cfg.Agent.RateLimit,
			WorkingDir: codePath, // Set working dir to source code location
		}
		return agent.NewCLIAgent(cliConfig), nil
	case "mock":
		return agent.NewMockAgent("decompile-agent"), nil
	default:
		// Check if command is specified for custom CLI agent
		if cfg.Agent.Command != "" {
			cliConfig := agent.CLIAgentConfig{
				Name:       "decompile-agent",
				Command:    cfg.Agent.Command,
				Args:       cfg.Agent.CLIArgs,
				Timeout:    cfg.Agent.Timeout,
				Retries:    cfg.Agent.Retries,
				RateLimit:  cfg.Agent.RateLimit,
				WorkingDir: codePath, // Set working dir to source code location
			}
			return agent.NewCLIAgent(cliConfig), nil
		} else {
			return nil, fmt.Errorf("unknown agent provider: %s", cfg.Agent.Provider)
		}
	}
}

func createDecompilePromptFromTemplate(sourcePath, outputPath string) (string, error) {
	// Use the decompile template
	tmpl, err := template.New("decompile").Parse(agent.DefaultPromptTemplates.Decompile)
	if err != nil {
		return "", fmt.Errorf("failed to parse decompile template: %w", err)
	}

	// Prepare template data
	data := agent.PromptData{
		SourcePath: sourcePath,
		OutputPath: outputPath,
	}

	// Execute template
	var buf bytes.Buffer
	if err := tmpl.Execute(&buf, data); err != nil {
		return "", fmt.Errorf("failed to execute decompile template: %w", err)
	}

	return buf.String(), nil
}