package agent

import (
	"testing"
	"time"

	"github.com/pboueri/intentc/src"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestCLIAgentFactory(t *testing.T) {
	factory := NewCLIAgentFactory(CLIAgentConfig{
		Command:   "echo",
		Timeout:   3 * time.Minute,
		Retries:   2,
		RateLimit: time.Second,
	})

	tests := []struct {
		name        string
		agentConfig src.Agent
		wantCommand string
		wantArgs    []string
		wantTimeout time.Duration
	}{
		{
			name: "basic CLI agent",
			agentConfig: src.Agent{
				Name: "test-cli",
				Type: "cli",
				Config: map[string]interface{}{
					"command": "amp",
					"timeout": "10m",
					"retries": float64(5),
				},
			},
			wantCommand: "amp",
			wantTimeout: 10 * time.Minute,
		},
		{
			name: "CLI agent with args",
			agentConfig: src.Agent{
				Name: "test-bash",
				Type: "cli",
				Config: map[string]interface{}{
					"command":  "bash",
					"cli_args": []interface{}{"script.sh", "--verbose"},
				},
			},
			wantCommand: "bash",
			wantArgs:    []string{"script.sh", "--verbose"},
		},
		{
			name: "default command from factory",
			agentConfig: src.Agent{
				Name: "test-default",
				Type: "cli",
				Config: map[string]interface{}{
					"timeout": "5m",
				},
			},
			wantCommand: "echo",
			wantTimeout: 5 * time.Minute,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			agent, err := factory.CreateAgent(tt.agentConfig)
			require.NoError(t, err)
			require.NotNil(t, agent)

			cliAgent, ok := agent.(*CLIAgent)
			require.True(t, ok)

			assert.Equal(t, tt.agentConfig.Name, cliAgent.GetName())
			assert.Equal(t, "cli", cliAgent.GetType())
			
			if tt.wantCommand != "" {
				assert.Equal(t, tt.wantCommand, cliAgent.command)
			}
			if tt.wantArgs != nil {
				assert.Equal(t, tt.wantArgs, cliAgent.args)
			}
			if tt.wantTimeout != 0 {
				assert.Equal(t, tt.wantTimeout, cliAgent.timeout)
			}
		})
	}
}

func TestClaudeAgentInheritance(t *testing.T) {
	// Test that ClaudeAgent properly inherits from CLIAgent
	claudeConfig := ClaudeAgentConfig{
		Timeout:   10 * time.Minute,
		Retries:   5,
		RateLimit: 2 * time.Second,
		CLIArgs:   []string{"--verbose"},
	}

	claude := NewClaudeAgent("test-claude", claudeConfig)
	
	// Verify it has all CLIAgent properties
	assert.Equal(t, "test-claude", claude.GetName())
	assert.Equal(t, "claude", claude.GetType())
	assert.Equal(t, "claude", claude.command)
	assert.Equal(t, 10*time.Minute, claude.timeout)
	assert.Equal(t, 5, claude.retries)
	assert.Equal(t, 2*time.Second, claude.rateLimit)
	assert.Equal(t, []string{"--verbose"}, claude.args)

	// Verify it has claude-specific templates
	assert.NotNil(t, claude.templates)
	assert.NotEmpty(t, claude.templates.Build)
	assert.NotEmpty(t, claude.templates.Refine)
	assert.NotEmpty(t, claude.templates.Validate)

	// Test template customization
	customTemplates := PromptTemplates{
		Build:    "CUSTOM BUILD",
		Refine:   "CUSTOM REFINE",
		Validate: "CUSTOM VALIDATE",
	}
	claude.SetTemplates(customTemplates)
	assert.Equal(t, customTemplates, claude.templates)
}

func TestCLIAgentTemplateUsage(t *testing.T) {
	// Test that both CLI and Claude agents can use custom templates
	cliAgent := NewCLIAgent(CLIAgentConfig{
		Name:    "test-cli",
		Command: "echo",
	})

	claudeAgent := NewClaudeAgent("test-claude", ClaudeAgentConfig{})

	// Both should start with default templates
	assert.Equal(t, DefaultPromptTemplates, cliAgent.templates)
	assert.Equal(t, DefaultPromptTemplates, claudeAgent.templates)

	// Both should be able to customize templates
	customTemplates := PromptTemplates{
		Build:    "{{.IntentName}} - Custom Build",
		Refine:   "{{.TargetName}} - Custom Refine",
		Validate: "{{.ValidationName}} - Custom Validate",
	}

	cliAgent.SetTemplates(customTemplates)
	claudeAgent.SetTemplates(customTemplates)

	assert.Equal(t, customTemplates, cliAgent.templates)
	assert.Equal(t, customTemplates, claudeAgent.templates)
}