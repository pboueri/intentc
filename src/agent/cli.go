package agent

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/kballard/go-shellquote"
	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/git"
	"github.com/pboueri/intentc/src/logger"
	"github.com/pboueri/intentc/src/util"
)

// CLIAgent is a generic agent that can execute any CLI command as a coding agent
type CLIAgent struct {
	name       string
	command    string
	args       []string
	timeout    time.Duration
	retries    int
	rateLimit  time.Duration
	workingDir string
	templates  PromptTemplates
}

// CLIAgentConfig contains configuration for creating a CLI agent
type CLIAgentConfig struct {
	Name       string
	Command    string
	Args       []string
	Timeout    time.Duration
	Retries    int
	RateLimit  time.Duration
	WorkingDir string
}

// NewCLIAgent creates a new CLI agent with the given configuration
func NewCLIAgent(config CLIAgentConfig) *CLIAgent {
	// Set defaults
	if config.Timeout == 0 {
		config.Timeout = 5 * time.Minute
	}
	if config.Retries == 0 {
		config.Retries = 3
	}
	if config.RateLimit == 0 {
		config.RateLimit = time.Second
	}
	if config.WorkingDir == "" {
		config.WorkingDir, _ = os.Getwd()
	}

	return &CLIAgent{
		name:       config.Name,
		command:    config.Command,
		args:       config.Args,
		timeout:    config.Timeout,
		retries:    config.Retries,
		rateLimit:  config.RateLimit,
		workingDir: config.WorkingDir,
		templates:  DefaultPromptTemplates,
	}
}

// GetName returns the agent's name
func (a *CLIAgent) GetName() string {
	return a.name
}

// GetType returns the agent type
func (a *CLIAgent) GetType() string {
	return "cli"
}

// Build implements the Agent interface
func (a *CLIAgent) Build(ctx context.Context, buildCtx BuildContext) ([]string, error) {
	logger.Info("[%s] Starting build for target: %s", a.name, buildCtx.Intent.Name)

	// Update working directory
	a.workingDir = buildCtx.ProjectRoot

	// Capture git status before execution
	var beforeStatus *git.GitStatus
	if buildCtx.GitManager != nil {
		status, err := buildCtx.GitManager.GetStatus(ctx)
		if err != nil {
			logger.Warn("Failed to get git status before build: %v", err)
		} else {
			beforeStatus = status
		}
	}

	// Create the prompt
	prompt := a.createBuildPrompt(buildCtx)

	// Log prompt preview in debug mode
	if logger.IsDebugEnabled() {
		promptPreview := prompt
		if len(promptPreview) > 300 {
			promptPreview = promptPreview[:300] + "..."
		}
		logger.Debug("[%s] Build prompt preview: %s", a.name, promptPreview)
	}

	// Execute with retries
	var lastErr error
	var output string
	var success bool
	for attempt := 1; attempt <= a.retries; attempt++ {
		if attempt > 1 {
			logger.Warn("[%s] Retrying build (attempt %d/%d) after error: %v", a.name, attempt, a.retries, lastErr)
			time.Sleep(a.rateLimit)
		}

		var err error
		output, err = a.executeCLI(ctx, prompt)
		if err == nil {
			success = true
			// For decompile, always show output at info level
			if buildCtx.Intent.Name == "decompile" && len(output) > 0 {
				logger.Info("[%s] Decompile completed. Agent output:\n%s", a.name, output)
			}
			break
		}
		lastErr = err
	}

	if !success {
		return nil, fmt.Errorf("%s agent failed after %d attempts: %w", a.name, a.retries, lastErr)
	}

	// Try parsing files from output first
	files := a.parseGeneratedFiles(output, buildCtx.ProjectRoot)

	// If no files found from output parsing and we have git status, use git detection
	if len(files) == 0 && beforeStatus != nil && buildCtx.GitManager != nil {
		detectedFiles, err := a.detectGeneratedFiles(ctx, buildCtx, beforeStatus)
		if err != nil {
			logger.Warn("Failed to detect files via git: %v", err)
		} else {
			files = detectedFiles
		}
	}

	logger.Info("[%s] Generated %d file(s)", a.name, len(files))
	return files, nil
}

// Refine implements the Agent interface
func (a *CLIAgent) Refine(ctx context.Context, target *src.Target, prompt string) error {
	logger.Info("[%s] Starting refinement for target: %s", a.name, target.Name)

	// Build refinement prompt
	var refinePrompt strings.Builder
	refinePrompt.WriteString("REFINEMENT REQUEST:\n")
	refinePrompt.WriteString(fmt.Sprintf("Target: %s\n", target.Name))
	refinePrompt.WriteString(fmt.Sprintf("User request: %s\n\n", prompt))
	refinePrompt.WriteString("Please refine the implementation based on the user's feedback.\n")

	// Execute refinement
	_, err := a.executeCLI(ctx, refinePrompt.String())
	return err
}

// Validate implements the Agent interface
func (a *CLIAgent) Validate(ctx context.Context, validation *src.Validation, generatedFiles []string) (bool, string, error) {
	logger.Info("[%s] Starting validation", a.name)

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
	output, err := a.executeCLI(ctx, valPrompt.String())
	if err != nil {
		return false, "", fmt.Errorf("validation check failed: %w", err)
	}

	// Parse result
	output = strings.TrimSpace(output)
	passed := strings.HasPrefix(strings.ToUpper(output), "PASS")

	return passed, output, nil
}

// executeCLI runs the CLI command with the given prompt and returns the output
func (a *CLIAgent) executeCLI(ctx context.Context, prompt string) (string, error) {
	// Create context with timeout
	cmdCtx, cancel := context.WithTimeout(ctx, a.timeout)
	defer cancel()

	// Log the prompt in debug mode
	logger.Debug("[%s] Prompt length: %d characters", a.name, len(prompt))
	if logger.IsDebugEnabled() {
		// Log first 500 chars of prompt or full prompt if shorter
		promptPreview := prompt
		if len(promptPreview) > 500 {
			promptPreview = fmt.Sprintf("%s...\n[truncated - showing first 500 of %d characters]",
				promptPreview[:500], len(prompt))
		}
		logger.Debug("[%s] Prompt content:\n%s", a.name, promptPreview)
	}

	// Create the shell command that pipes echo output to the agent command
	// Use sh -c to execute the pipe command
	quotedPrompt := shellquote.Join(prompt)
	shellCmd := fmt.Sprintf("echo %s | %s %s",
		quotedPrompt,
		a.command,
		strings.Join(a.args, " "))

	// Execute using sh -c
	cmd := exec.CommandContext(cmdCtx, "sh", "-c", shellCmd)
	cmd.Dir = a.workingDir

	// Set up output capture
	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	// Log the command being executed (replace prompt with placeholder for cleaner logs)
	shellCmdForLog := fmt.Sprintf("echo {PROMPT} | %s %s", a.command, strings.Join(a.args, " "))
	logger.Debug("[%s] Executing shell command: sh -c %s", a.name, shellCmdForLog)

	// Run the command
	err := cmd.Run()

	if err != nil {
		if cmdCtx.Err() == context.DeadlineExceeded {
			return "", fmt.Errorf("%s command timed out after %v", a.command, a.timeout)
		}
		return "", fmt.Errorf("%s command failed: %w\nstderr: %s", a.command, err, stderr.String())
	}

	output := stdout.String()

	// Log the output in debug mode
	if logger.IsDebugEnabled() && len(output) > 0 {
		outputPreview := output
		if len(outputPreview) > 1000 {
			outputPreview = fmt.Sprintf("%s...\n[truncated - showing first 1000 of %d characters]",
				outputPreview[:1000], len(output))
		}
		logger.Debug("[%s] Command output:\n%s", a.name, outputPreview)
	}

	return output, nil
}

// Decompile implements the Decompiler interface
func (a *CLIAgent) Decompile(ctx context.Context, decompileCtx DecompileContext) ([]string, error) {
	logger.Info("[%s] Starting decompile of codebase: %s", a.name, decompileCtx.SourcePath)
	logger.Info("[%s] Output directory: %s", a.name, decompileCtx.OutputPath)

	// Save original working directory and restore it after
	originalWorkingDir := a.workingDir
	defer func() {
		a.workingDir = originalWorkingDir
	}()

	// Set working directory to source path for analysis
	a.workingDir = decompileCtx.SourcePath

	// Create the decompile prompt
	prompt, err := a.createDecompilePrompt(decompileCtx)
	if err != nil {
		return nil, fmt.Errorf("failed to create decompile prompt: %w", err)
	}

	// Log prompt preview in debug mode
	if logger.IsDebugEnabled() {
		promptPreview := prompt
		if len(promptPreview) > 300 {
			promptPreview = promptPreview[:300] + "..."
		}
		logger.Debug("[%s] Decompile prompt preview: %s", a.name, promptPreview)
	}

	// Execute with retries
	var lastErr error
	var output string
	var success bool
	for attempt := 1; attempt <= a.retries; attempt++ {
		if attempt > 1 {
			logger.Warn("[%s] Retrying decompile (attempt %d/%d) after error: %v", a.name, attempt, a.retries, lastErr)
			time.Sleep(a.rateLimit)
		}

		var err error
		output, err = a.executeCLI(ctx, prompt)
		if err == nil {
			success = true
			// Always show decompile output at info level
			if len(output) > 0 {
				logger.Info("[%s] Decompile completed. Agent output:\n%s", a.name, output)
			}
			break
		}
		lastErr = err
	}

	if !success {
		return nil, fmt.Errorf("%s agent failed after %d attempts: %w", a.name, a.retries, lastErr)
	}

	// Parse generated files from output
	files := a.parseGeneratedFiles(output, decompileCtx.OutputPath)

	// If no files found from output parsing, scan the output directory
	if len(files) == 0 {
		logger.Debug("No files reported by agent, scanning output directory for .ic and .icv files...")
		var foundFiles []string
		err = filepath.Walk(decompileCtx.OutputPath, func(path string, info os.FileInfo, err error) error {
			if err != nil {
				return nil
			}
			if !info.IsDir() && (strings.HasSuffix(path, ".ic") || strings.HasSuffix(path, ".icv")) {
				foundFiles = append(foundFiles, path)
			}
			return nil
		})
		if err != nil {
			logger.Warn("Failed to scan output directory: %v", err)
		} else if len(foundFiles) > 0 {
			files = foundFiles
			logger.Debug("Found %d intent files in output directory", len(foundFiles))
		}
	}

	logger.Info("[%s] Generated %d file(s)", a.name, len(files))
	return files, nil
}

// createDecompilePrompt creates the prompt for decompiling
func (a *CLIAgent) createDecompilePrompt(decompileCtx DecompileContext) (string, error) {
	// Use the decompile template
	data := PromptData{
		SourcePath: decompileCtx.SourcePath,
		OutputPath: decompileCtx.OutputPath,
	}

	prompt, err := ExecuteTemplate(a.templates.Decompile, data)
	if err != nil {
		return "", fmt.Errorf("failed to create decompile prompt: %w", err)
	}

	return prompt, nil
}

// createBuildPrompt creates the prompt for building
func (a *CLIAgent) createBuildPrompt(buildCtx BuildContext) string {
	// Use common template data preparation
	data := PrepareTemplateData(buildCtx)
	data["WorkingDir"] = a.workingDir

	prompt, err := ExecuteTemplate(a.templates.Build, data)
	if err != nil {
		logger.Error("Failed to create build prompt: %v", err)
		// Fallback to simple prompt
		return fmt.Sprintf("Target: %s\n\n%s", buildCtx.Intent.Name, buildCtx.Intent.Content)
	}

	return prompt
}

// detectGeneratedFiles uses git status to detect newly created or modified files
func (a *CLIAgent) detectGeneratedFiles(ctx context.Context, buildCtx BuildContext, beforeStatus *git.GitStatus) ([]string, error) {
	// Get current git status
	afterStatus, err := buildCtx.GitManager.GetStatus(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get git status: %w", err)
	}

	var generatedFiles []string

	// Collect all new untracked files
	generatedFiles = append(generatedFiles, afterStatus.UntrackedFiles...)

	// Collect modified files that weren't modified before
	beforeModified := make(map[string]bool)
	for _, file := range beforeStatus.ModifiedFiles {
		beforeModified[file] = true
	}

	for _, file := range afterStatus.ModifiedFiles {
		if !beforeModified[file] {
			generatedFiles = append(generatedFiles, file)
		}
	}

	// Convert to absolute paths
	for i, file := range generatedFiles {
		generatedFiles[i] = filepath.Join(buildCtx.ProjectRoot, file)
	}

	return generatedFiles, nil
}

// parseGeneratedFiles extracts file paths from the CLI output
func (a *CLIAgent) parseGeneratedFiles(output string, projectRoot string) []string {
	files := []string{}
	lines := strings.Split(output, "\n")

	logger.Debug("Parsing output for generated files (projectRoot: %s)", projectRoot)

	for _, line := range lines {
		line = strings.TrimSpace(line)

		// Look for common patterns that indicate file creation
		if strings.Contains(line, "Created") || strings.Contains(line, "Generated") ||
			strings.Contains(line, "Wrote") || strings.Contains(line, "Writing") || strings.Contains(line, "Modified") {
			logger.Debug("Found creation line: %s", line)
			// Extract file paths (this is a simple heuristic)
			parts := strings.Fields(line)
			for _, part := range parts {
				// Check if it looks like a file path
				if strings.Contains(part, "/") || strings.Contains(part, ".") {
					// Use utility to clean up the path
					path := util.CleanFilePath(part)

					// Make it absolute if relative
					path = util.MakeAbsolute(path, projectRoot)

					logger.Debug("Found potential file path in output: %s (absolute: %s)", part, path)

					// Add the file path (in production, files would exist)
					files = append(files, path)
				}
			}
		}
	}

	// Remove duplicates
	seen := make(map[string]bool)
	unique := []string{}
	for _, file := range files {
		if !seen[file] {
			seen[file] = true
			unique = append(unique, file)
		}
	}

	return unique
}

// SetTemplates allows customizing the prompt templates
func (a *CLIAgent) SetTemplates(templates PromptTemplates) {
	a.templates = templates
}

// CLIAgentFactory implements AgentFactory for CLI agents
type CLIAgentFactory struct {
	defaultConfig CLIAgentConfig
}

func NewCLIAgentFactory(config CLIAgentConfig) *CLIAgentFactory {
	return &CLIAgentFactory{
		defaultConfig: config,
	}
}

func (f *CLIAgentFactory) CreateAgent(config src.Agent) (Agent, error) {
	// Start with default config
	agentConfig := f.defaultConfig
	agentConfig.Name = config.Name

	if config.Config != nil {
		// Override with agent-specific settings
		if command, ok := config.Config["command"].(string); ok {
			agentConfig.Command = command
		}
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
		if cliArgs, ok := config.Config["cli_args"].([]interface{}); ok {
			agentConfig.Args = []string{}
			for _, arg := range cliArgs {
				if str, ok := arg.(string); ok {
					agentConfig.Args = append(agentConfig.Args, str)
				}
			}
		}
	}

	return NewCLIAgent(agentConfig), nil
}

func (f *CLIAgentFactory) GetSupportedTypes() []string {
	return []string{"cli"}
}
