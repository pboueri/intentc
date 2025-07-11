package agent

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/logger"
)

// ClaudeAgent extends CLIAgent with Claude-specific functionality
type ClaudeAgent struct {
	*CLIAgent
	templates PromptTemplates
}

// ClaudeAgentConfig contains configuration for creating a Claude agent
type ClaudeAgentConfig struct {
	Timeout   time.Duration
	Retries   int
	RateLimit time.Duration
	CLIArgs   []string
}

// NewClaudeAgent creates a new Claude agent with the given configuration
func NewClaudeAgent(name string, config ClaudeAgentConfig) *ClaudeAgent {
	// Create CLI agent config
	cliConfig := CLIAgentConfig{
		Name:      name,
		Command:   "claude",
		Args:      config.CLIArgs,
		Timeout:   config.Timeout,
		Retries:   config.Retries,
		RateLimit: config.RateLimit,
	}

	return &ClaudeAgent{
		CLIAgent:  NewCLIAgent(cliConfig),
		templates: DefaultPromptTemplates,
	}
}

// GetType returns the agent type
func (c *ClaudeAgent) GetType() string {
	return "claude"
}

// Build overrides the CLIAgent Build method to use Claude-specific prompts
func (c *ClaudeAgent) Build(ctx context.Context, buildCtx BuildContext) ([]string, error) {
	logger.Info("=== Claude Agent: Building target with Generation ID: %s ===", buildCtx.GenerationID)
	logger.Info("Timeout: %v, Retries: %d", c.timeout, c.retries)
	logger.Info("Target: %s", buildCtx.Intent.Name)

	// Update working directory
	c.workingDir = buildCtx.ProjectRoot

	// Create Claude-specific prompt using template
	prompt, err := c.createClaudeBuildPrompt(buildCtx)
	if err != nil {
		return nil, fmt.Errorf("failed to create build prompt: %w", err)
	}

	// Show first 200 chars of prompt for debugging
	promptPreview := prompt
	if len(promptPreview) > 200 {
		promptPreview = promptPreview[:200] + "..."
	}
	logger.Debug("Prompt preview: %s", promptPreview)
	logger.Info("=== Starting execution ===")

	// Execute with retries
	var lastErr error
	for attempt := 1; attempt <= c.retries; attempt++ {
		if attempt > 1 {
			logger.Warn("[%s] Retrying build (attempt %d/%d) after error: %v", c.name, attempt, c.retries, lastErr)
			time.Sleep(c.rateLimit)
		}

		output, err := c.executeCLI(ctx, prompt)
		if err == nil {
			// Parse the generated files from the output
			files := c.parseClaudeGeneratedFiles(output, buildCtx)

			logger.Info("=== Claude Agent: Completed successfully ===")
			logger.Info("Generated %d file(s)", len(files))

			return files, nil
		}
		lastErr = err
	}

	return nil, fmt.Errorf("claude agent failed after %d attempts: %w", c.retries, lastErr)
}

// Refine overrides the CLIAgent Refine method to use Claude-specific prompts
func (c *ClaudeAgent) Refine(ctx context.Context, target *src.Target, userPrompt string) error {
	// Use common template data preparation
	data := PrepareRefinementData(target, userPrompt, nil, nil)

	prompt, err := ExecuteTemplate(c.templates.Refine, data)
	if err != nil {
		return fmt.Errorf("failed to create refine prompt: %w", err)
	}

	// Execute refinement
	output, err := c.executeCLI(ctx, prompt)
	if err != nil {
		return fmt.Errorf("refinement failed: %w", err)
	}

	logger.Info("Refinement output:\n%s", output)
	return nil
}

// Validate overrides the CLIAgent Validate method to use Claude-specific prompts
func (c *ClaudeAgent) Validate(ctx context.Context, validation *src.Validation, generatedFiles []string) (bool, string, error) {
	// Use common template data preparation
	data := PrepareValidationData(validation, generatedFiles)

	prompt, err := ExecuteTemplate(c.templates.Validate, data)
	if err != nil {
		return false, "", fmt.Errorf("failed to create validate prompt: %w", err)
	}

	// Execute validation
	output, err := c.executeCLI(ctx, prompt)
	if err != nil {
		return false, "", fmt.Errorf("validation check failed: %w", err)
	}

	// Parse result
	output = strings.TrimSpace(output)
	passed := strings.HasPrefix(strings.ToUpper(output), "PASS")

	return passed, output, nil
}

// createClaudeBuildPrompt creates a Claude-specific build prompt using the template
func (c *ClaudeAgent) createClaudeBuildPrompt(buildCtx BuildContext) (string, error) {
	// Use common template data preparation
	data := PrepareTemplateData(buildCtx)
	
	return ExecuteTemplate(c.templates.Build, data)
}

// parseClaudeGeneratedFiles provides Claude-specific file parsing logic
func (c *ClaudeAgent) parseClaudeGeneratedFiles(output string, buildCtx BuildContext) []string {
	logger.Debug("=== Parsing generated files from output ===")

	// Use the parent CLIAgent's parser
	files := c.CLIAgent.parseGeneratedFiles(output, buildCtx.ProjectRoot)

	if len(files) > 0 {
		for _, file := range files {
			logger.Debug("  Found file: %s", file)
		}
	}

	return files
}

// SetTemplates allows customizing the prompt templates
func (c *ClaudeAgent) SetTemplates(templates PromptTemplates) {
	c.templates = templates
}

// Decompile implements the Decompiler interface
func (c *ClaudeAgent) Decompile(ctx context.Context, decompileCtx DecompileContext) ([]string, error) {
	// Claude agent delegates to the embedded CLI agent for decompile
	return c.CLIAgent.Decompile(ctx, decompileCtx)
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
		if cliArgs, ok := config.Config["cli_args"].([]interface{}); ok {
			agentConfig.CLIArgs = []string{}
			for _, arg := range cliArgs {
				if str, ok := arg.(string); ok {
					agentConfig.CLIArgs = append(agentConfig.CLIArgs, str)
				}
			}
		}
	}

	return NewClaudeAgent(config.Name, agentConfig), nil
}

func (f *ClaudeAgentFactory) GetSupportedTypes() []string {
	return []string{"claude"}
}
