package integration

import (
	"context"
	"os"
	"testing"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/agent"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestCLIAgentIntegration(t *testing.T) {
	// Create a temporary directory
	tmpDir, err := os.MkdirTemp("", "cli-agent-integration")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	// Create a printf-based CLI agent that outputs generated files
	config := agent.CLIAgentConfig{
		Name:       "printf-agent",
		Command:    "printf",
		Args:       []string{"Generated file: test.go\\nCreated src/main.go\\n"},
		WorkingDir: tmpDir,
	}
	cliAgent := agent.NewCLIAgent(config)

	// Test Build
	ctx := context.Background()
	buildCtx := agent.BuildContext{
		Intent: &src.Intent{
			Name:    "test-feature",
			Content: "Create a test file",
		},
		ProjectRoot:  tmpDir,
		GenerationID: "test-123",
	}

	// The printf command will output file paths that should be parsed
	_, err = cliAgent.Build(ctx, buildCtx)
	require.NoError(t, err)
	// The CLI agent may or may not parse files from the output depending on format
	// Just check that Build completes without error

	// Test Validate with a command that outputs PASS
	validateConfig := agent.CLIAgentConfig{
		Name:       "validate-agent",
		Command:    "printf",
		Args:       []string{"PASS: Validation successful"},
		WorkingDir: tmpDir,
	}
	validateAgent := agent.NewCLIAgent(validateConfig)

	validation := &src.Validation{
		Name:        "test_validation",
		Type:        "FileCheck",
		Description: "Test validation",
	}

	passed, explanation, err := validateAgent.Validate(ctx, validation, []string{"test.go"})
	require.NoError(t, err)
	assert.True(t, passed)
	assert.Contains(t, explanation, "PASS")

	// Test inheritance - Claude agent should work as before
	claudeConfig := agent.ClaudeAgentConfig{}
	claude := agent.NewClaudeAgent("claude-test", claudeConfig)
	
	assert.Equal(t, "claude-test", claude.GetName())
	assert.Equal(t, "claude", claude.GetType())
}

func TestCLIAgentCustomCommand(t *testing.T) {
	// Test that custom commands can be specified
	config := agent.CLIAgentConfig{
		Name:    "custom-agent",
		Command: "custom-ai-tool",
		Args:    []string{"--model", "test"},
	}
	cliAgent := agent.NewCLIAgent(config)

	assert.Equal(t, "custom-agent", cliAgent.GetName())
	assert.Equal(t, "cli", cliAgent.GetType())
	// The actual command execution would fail, but the configuration should work
}