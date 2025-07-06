package agent

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"io"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/logger"
)

type ClaudeAgent struct {
	name      string
	timeout   time.Duration
	retries   int
	rateLimit time.Duration
	cliArgs   []string
}

type ClaudeAgentConfig struct {
	Timeout   time.Duration
	Retries   int
	RateLimit time.Duration
	CLIArgs   []string
}

func NewClaudeAgent(name string, config ClaudeAgentConfig) *ClaudeAgent {
	if config.Timeout == 0 {
		config.Timeout = 5 * time.Minute
	}
	if config.Retries == 0 {
		config.Retries = 3
	}
	if config.RateLimit == 0 {
		config.RateLimit = 1 * time.Second
	}

	return &ClaudeAgent{
		name:      name,
		timeout:   config.Timeout,
		retries:   config.Retries,
		rateLimit: config.RateLimit,
		cliArgs:   config.CLIArgs,
	}
}

func (c *ClaudeAgent) GetName() string {
	return c.name
}

func (c *ClaudeAgent) GetType() string {
	return "claude"
}

func (c *ClaudeAgent) Build(ctx context.Context, buildCtx BuildContext) ([]string, error) {
	// Create prompt from intent
	prompt := c.createBuildPrompt(buildCtx)

	logger.Info("=== Claude Agent: Building target with Generation ID: %s ===", buildCtx.GenerationID)
	logger.Info("Timeout: %v, Retries: %d", c.timeout, c.retries)
	logger.Info("Target: %s", buildCtx.Intent.Name)
	
	// Show first 200 chars of prompt for debugging
	promptPreview := prompt
	if len(promptPreview) > 200 {
		promptPreview = promptPreview[:200] + "..."
	}
	logger.Debug("Prompt preview: %s", promptPreview)
	logger.Info("=== Starting execution ===")

	// Execute with retries
	var output string
	var err error
	for i := 0; i <= c.retries; i++ {
		if i > 0 {
			// Rate limiting between retries
			select {
			case <-time.After(c.rateLimit):
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		}

		if i > 0 {
			logger.Info("=== Retry attempt %d/%d ===", i, c.retries)
		}

		output, err = c.executeClaudeCLI(ctx, prompt, buildCtx.ProjectRoot)
		if err == nil {
			break
		}

		if i < c.retries {
			logger.Warn("Retry %d/%d after error: %v", i+1, c.retries, err)
		}
	}

	if err != nil {
		return nil, fmt.Errorf("claude agent failed after %d retries: %w", c.retries, err)
	}

	// Parse the generated files from the output
	files := c.parseGeneratedFiles(output, buildCtx)
	
	logger.Info("=== Claude Agent: Completed successfully ===")
	logger.Info("Generated %d file(s)", len(files))
	
	return files, nil
}

func (c *ClaudeAgent) createBuildPrompt(buildCtx BuildContext) string {
	var prompt strings.Builder

	// System context
	prompt.WriteString("You are an AI coding assistant helping to implement features for a project using intentc, ")
	prompt.WriteString("a tool that transforms high-level intents into working code.\n\n")

	// Project context
	prompt.WriteString(fmt.Sprintf("Project root: %s\n", buildCtx.ProjectRoot))
	prompt.WriteString(fmt.Sprintf("Generation ID: %s\n\n", buildCtx.GenerationID))

	// Intent information
	prompt.WriteString("INTENT TO IMPLEMENT:\n")
	prompt.WriteString(fmt.Sprintf("Target: %s\n", buildCtx.Intent.Name))
	if len(buildCtx.Intent.Dependencies) > 0 {
		prompt.WriteString(fmt.Sprintf("Dependencies: %s\n", strings.Join(buildCtx.Intent.Dependencies, ", ")))
	}
	prompt.WriteString("\nIntent content:\n")
	prompt.WriteString(buildCtx.Intent.Content)
	prompt.WriteString("\n\n")

	// Validation constraints
	if len(buildCtx.Validations) > 0 {
		prompt.WriteString("\nVALIDATION CONSTRAINTS:\n")
		prompt.WriteString("Please ensure the generated code meets these requirements:\n\n")
		
		for _, valFile := range buildCtx.Validations {
			for _, val := range valFile.Validations {
				prompt.WriteString(fmt.Sprintf("- %s (%s): %s\n", val.Name, val.Type, val.Description))
				if params, ok := val.Parameters["Details"].(string); ok && params != "" {
					prompt.WriteString(fmt.Sprintf("  Details: %s\n", params))
				}
			}
		}
		prompt.WriteString("\n")
	}

	// Instructions
	prompt.WriteString("INSTRUCTIONS:\n")
	prompt.WriteString("1. Generate the code to implement all the features described above\n")
	prompt.WriteString("2. Create all necessary files and directories\n")
	prompt.WriteString("3. Follow best practices for the programming language and framework\n")
	prompt.WriteString("4. Ensure the code meets all validation constraints\n")
	prompt.WriteString("5. Focus on user experience and product quality\n")
	prompt.WriteString("6. Include error handling and appropriate logging\n")
	prompt.WriteString("7. Write clean, maintainable code with clear comments where necessary\n\n")

	prompt.WriteString("Please generate the complete implementation for this intent.")

	return prompt.String()
}

func (c *ClaudeAgent) executeClaudeCLI(ctx context.Context, prompt, workDir string) (string, error) {
	// Create command with timeout
	ctx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()

	// Use claude CLI directly with the prompt via stdin
	args := append([]string{}, c.cliArgs...)
	cmd := exec.CommandContext(ctx, "claude", args...)
	cmd.Dir = workDir
	cmd.Stdin = strings.NewReader(prompt)

	// Capture output
	var stdout, stderr bytes.Buffer
	
	// Stream output when info level is enabled
	logger.Info("=== Executing Claude CLI in directory: %s ===", workDir)
	logger.Info("Command: claude %s", strings.Join(args, " "))
	logger.Info("=== Claude CLI Output ===")
	
	// Create pipes for streaming output
	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		return "", fmt.Errorf("failed to create stdout pipe: %w", err)
	}
	stderrPipe, err := cmd.StderrPipe()
	if err != nil {
		return "", fmt.Errorf("failed to create stderr pipe: %w", err)
	}
	
	// Start the command
	if err := cmd.Start(); err != nil {
		return "", fmt.Errorf("failed to start claude CLI: %w", err)
	}
	
	// Create TeeReaders to both capture and stream output
	stdoutTee := io.TeeReader(stdoutPipe, &stdout)
	stderrTee := io.TeeReader(stderrPipe, &stderr)
	
	// Copy output in goroutines for concurrent streaming
	errChan := make(chan error, 2)
	
	go func() {
		scanner := bufio.NewScanner(stdoutTee)
		for scanner.Scan() {
			logger.Info("%s", scanner.Text())
		}
		errChan <- scanner.Err()
	}()
	
	go func() {
		scanner := bufio.NewScanner(stderrTee)
		for scanner.Scan() {
			logger.Warn("[stderr] %s", scanner.Text())
		}
		errChan <- scanner.Err()
	}()
	
	// Wait for command to complete
	cmdErr := cmd.Wait()
	
	// Wait for output goroutines to finish
	for i := 0; i < 2; i++ {
		if err := <-errChan; err != nil && err != io.EOF {
			logger.Warn("Error copying output: %v", err)
		}
	}
	
	logger.Info("=== Claude CLI Execution Complete ===")
	
	if cmdErr != nil {
		if ctx.Err() == context.DeadlineExceeded {
			return "", fmt.Errorf("claude CLI timed out after %v", c.timeout)
		}
		return "", fmt.Errorf("claude CLI failed: %w\nstderr: %s", cmdErr, stderr.String())
	}

	return stdout.String(), nil
}

func (c *ClaudeAgent) parseGeneratedFiles(output string, buildCtx BuildContext) []string {
	var files []string
	
	logger.Debug("=== Parsing generated files from output ===")
	
	// Look for file paths in the output
	// This is a simple implementation - in reality, we'd need to parse
	// the Claude output more carefully to extract file paths
	lines := strings.Split(output, "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		// Look for lines that might indicate file creation
		if strings.Contains(line, "Created file:") ||
			strings.Contains(line, "Generated:") ||
			strings.Contains(line, "Writing to:") {
			// Extract the file path
			parts := strings.Split(line, ":")
			if len(parts) >= 2 {
				filePath := strings.TrimSpace(parts[len(parts)-1])
				// Make it relative to project root
				if !filepath.IsAbs(filePath) {
					filePath = filepath.Join(buildCtx.ProjectRoot, filePath)
				}
				files = append(files, filePath)
				logger.Debug("  Found file: %s", filePath)
			}
		}
	}

	// If we couldn't parse specific files, generate expected file paths
	// based on the intent name
	if len(files) == 0 {
		targetDir := filepath.Join(buildCtx.ProjectRoot, buildCtx.Intent.Name)
		files = append(files, targetDir)
		logger.Debug("  No specific files found, using default: %s", targetDir)
	}

	return files
}

func (c *ClaudeAgent) Refine(ctx context.Context, target *src.Target, prompt string) error {
	// Build refinement prompt
	var refinePrompt strings.Builder
	refinePrompt.WriteString("REFINEMENT REQUEST:\n")
	refinePrompt.WriteString(fmt.Sprintf("Target: %s\n", target.Name))
	refinePrompt.WriteString(fmt.Sprintf("User request: %s\n\n", prompt))
	refinePrompt.WriteString("Please refine the implementation based on the user's feedback.\n")

	// Execute refinement
	output, err := c.executeClaudeCLI(ctx, refinePrompt.String(), ".")
	if err != nil {
		return fmt.Errorf("refinement failed: %w", err)
	}

	logger.Info("Refinement output:\n%s", output)
	return nil
}

func (c *ClaudeAgent) Validate(ctx context.Context, validation *src.Validation, generatedFiles []string) (bool, string, error) {
	// Build validation prompt
	var valPrompt strings.Builder
	valPrompt.WriteString("VALIDATION REQUEST:\n")
	valPrompt.WriteString(fmt.Sprintf("Name: %s\n", validation.Name))
	valPrompt.WriteString(fmt.Sprintf("Type: %s\n", validation.Type))
	valPrompt.WriteString(fmt.Sprintf("Description: %s\n", validation.Description))
	if details, ok := validation.Parameters["Details"].(string); ok && details != "" {
		valPrompt.WriteString(fmt.Sprintf("Details: %s\n", details))
	}
	valPrompt.WriteString("\nGenerated files:\n")
	for _, file := range generatedFiles {
		valPrompt.WriteString(fmt.Sprintf("- %s\n", file))
	}
	valPrompt.WriteString("\nPlease verify if the generated code meets this validation constraint. ")
	valPrompt.WriteString("Respond with 'PASS' or 'FAIL' followed by an explanation.\n")

	// Execute validation
	output, err := c.executeClaudeCLI(ctx, valPrompt.String(), ".")
	if err != nil {
		return false, "", fmt.Errorf("validation check failed: %w", err)
	}

	// Parse result
	output = strings.TrimSpace(output)
	passed := strings.HasPrefix(strings.ToUpper(output), "PASS")
	
	return passed, output, nil
}

// ClaudeAgentFactory implements AgentFactory for Claude agents
type ClaudeAgentFactory struct {
	defaultConfig ClaudeAgentConfig
}

func NewClaudeAgentFactory(config ClaudeAgentConfig) *ClaudeAgentFactory {
	return &ClaudeAgentFactory{
		defaultConfig: config,
	}
}

func (f *ClaudeAgentFactory) CreateAgent(config src.Agent) (Agent, error) {
	// Parse agent-specific configuration
	agentConfig := f.defaultConfig
	
	if config.Config != nil {
		// Override with agent-specific settings
		if timeout, ok := config.Config["timeout"].(string); ok {
			if d, err := time.ParseDuration(timeout); err == nil {
				agentConfig.Timeout = d
			}
		}
		if retries, ok := config.Config["retries"].(float64); ok {
			agentConfig.Retries = int(retries)
		}
		if rateLimit, ok := config.Config["rate_limit"].(string); ok {
			if d, err := time.ParseDuration(rateLimit); err == nil {
				agentConfig.RateLimit = d
			}
		}
	}

	return NewClaudeAgent(config.Name, agentConfig), nil
}

func (f *ClaudeAgentFactory) GetSupportedTypes() []string {
	return []string{"claude"}
}

// Register the Claude agent factory
func init() {
	// This will be called from main or a setup function
	// registry.RegisterFactory("claude", NewClaudeAgentFactory(ClaudeAgentConfig{}))
}