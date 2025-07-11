package agent

import (
	"fmt"
	
	"github.com/pboueri/intentc/src/config"
)

// CreateFromConfig creates an agent based on the provided configuration
func CreateFromConfig(cfg *config.Config, name string) (Agent, error) {
	if name == "" {
		name = "default-agent"
	}

	switch cfg.Agent.Provider {
	case "claude":
		// For backwards compatibility, claude provider uses the ClaudeAgent
		claudeConfig := ClaudeAgentConfig{
			Timeout:   cfg.Agent.Timeout,
			Retries:   cfg.Agent.Retries,
			RateLimit: cfg.Agent.RateLimit,
			CLIArgs:   cfg.Agent.CLIArgs,
		}
		return NewClaudeAgent(name, claudeConfig), nil
		
	case "cli":
		// Generic CLI agent
		if cfg.Agent.Command == "" {
			return nil, fmt.Errorf("CLI agent requires 'command' to be specified in config")
		}
		cliConfig := CLIAgentConfig{
			Name:      name,
			Command:   cfg.Agent.Command,
			Args:      cfg.Agent.CLIArgs,
			Timeout:   cfg.Agent.Timeout,
			Retries:   cfg.Agent.Retries,
			RateLimit: cfg.Agent.RateLimit,
		}
		return NewCLIAgent(cliConfig), nil
		
	case "mock":
		return NewMockAgent(name), nil
		
	default:
		// Check if command is specified for custom CLI agent
		if cfg.Agent.Command != "" {
			cliConfig := CLIAgentConfig{
				Name:      name,
				Command:   cfg.Agent.Command,
				Args:      cfg.Agent.CLIArgs,
				Timeout:   cfg.Agent.Timeout,
				Retries:   cfg.Agent.Retries,
				RateLimit: cfg.Agent.RateLimit,
			}
			return NewCLIAgent(cliConfig), nil
		}
		return nil, fmt.Errorf("unknown agent provider: %s", cfg.Agent.Provider)
	}
}

// CreateFromConfigWithWorkingDir creates an agent with a specific working directory
func CreateFromConfigWithWorkingDir(cfg *config.Config, name string, workingDir string) (Agent, error) {
	if name == "" {
		name = "default-agent"
	}

	switch cfg.Agent.Provider {
	case "claude":
		// Use CLI agent directly for decompile to avoid template system
		cliConfig := CLIAgentConfig{
			Name:       name,
			Command:    "claude",
			Args:       cfg.Agent.CLIArgs,
			Timeout:    cfg.Agent.Timeout,
			Retries:    cfg.Agent.Retries,
			RateLimit:  cfg.Agent.RateLimit,
			WorkingDir: workingDir,
		}
		return NewCLIAgent(cliConfig), nil
		
	case "cli":
		// Generic CLI agent
		if cfg.Agent.Command == "" {
			return nil, fmt.Errorf("CLI agent requires 'command' to be specified in config")
		}
		cliConfig := CLIAgentConfig{
			Name:       name,
			Command:    cfg.Agent.Command,
			Args:       cfg.Agent.CLIArgs,
			Timeout:    cfg.Agent.Timeout,
			Retries:    cfg.Agent.Retries,
			RateLimit:  cfg.Agent.RateLimit,
			WorkingDir: workingDir,
		}
		return NewCLIAgent(cliConfig), nil
		
	case "mock":
		return NewMockAgent(name), nil
		
	default:
		// Check if command is specified for custom CLI agent
		if cfg.Agent.Command != "" {
			cliConfig := CLIAgentConfig{
				Name:       name,
				Command:    cfg.Agent.Command,
				Args:       cfg.Agent.CLIArgs,
				Timeout:    cfg.Agent.Timeout,
				Retries:    cfg.Agent.Retries,
				RateLimit:  cfg.Agent.RateLimit,
				WorkingDir: workingDir,
			}
			return NewCLIAgent(cliConfig), nil
		}
		return nil, fmt.Errorf("unknown agent provider: %s", cfg.Agent.Provider)
	}
}