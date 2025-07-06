package agent

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/pboueri/intentc/src"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestNewClaudeAgent(t *testing.T) {
	tests := []struct {
		name     string
		config   ClaudeAgentConfig
		expected ClaudeAgent
	}{
		{
			name:   "default config",
			config: ClaudeAgentConfig{},
			expected: ClaudeAgent{
				name:      "test-agent",
				timeout:   5 * time.Minute,
				retries:   3,
				rateLimit: 1 * time.Second,
			},
		},
		{
			name: "custom config",
			config: ClaudeAgentConfig{
				Timeout:   10 * time.Minute,
				Retries:   5,
				RateLimit: 2 * time.Second,
			},
			expected: ClaudeAgent{
				name:      "test-agent",
				timeout:   10 * time.Minute,
				retries:   5,
				rateLimit: 2 * time.Second,
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			agent := NewClaudeAgent("test-agent", tt.config)
			assert.Equal(t, tt.expected.name, agent.name)
			assert.Equal(t, tt.expected.timeout, agent.timeout)
			assert.Equal(t, tt.expected.retries, agent.retries)
			assert.Equal(t, tt.expected.rateLimit, agent.rateLimit)
		})
	}
}

func TestClaudeAgent_GetName(t *testing.T) {
	agent := NewClaudeAgent("my-claude", ClaudeAgentConfig{})
	assert.Equal(t, "my-claude", agent.GetName())
}

func TestClaudeAgent_GetType(t *testing.T) {
	agent := NewClaudeAgent("test", ClaudeAgentConfig{})
	assert.Equal(t, "claude", agent.GetType())
}

func TestClaudeAgent_createBuildPrompt(t *testing.T) {
	agent := NewClaudeAgent("test", ClaudeAgentConfig{})
	
	buildCtx := BuildContext{
		Intent: &src.Intent{
			Name:         "cmd/app",
			Dependencies: []string{"core", "utils"},
			Content: `# Simple CLI Application

## Features

### Hello Command
Add a hello command that greets the user.
The command should accept a --name flag.

### Goodbye Command  
Add a goodbye command that says farewell.
`,
		},
		Validations: []*src.ValidationFile{
			{
				Validations: []src.Validation{
					{
						Name:        "main-file-exists",
						Type:        src.ValidationTypeFileCheck,
						Description: "Main command file should exist",
						Parameters: map[string]interface{}{
							"Details": "cmd/app/main.go",
						},
					},
					{
						Name:        "command-works",
						Type:        src.ValidationTypeCommandLineCheck,
						Description: "Hello command should work",
						Parameters:  map[string]interface{}{},
					},
				},
			},
		},
		ProjectRoot:  "/test/project",
		GenerationID: "gen-123",
	}

	prompt := agent.createBuildPrompt(buildCtx)

	// Verify prompt contains expected elements
	assert.Contains(t, prompt, "intentc")
	assert.Contains(t, prompt, "Project root: /test/project")
	assert.Contains(t, prompt, "Generation ID: gen-123")
	assert.Contains(t, prompt, "Target: cmd/app")
	assert.Contains(t, prompt, "Dependencies: core, utils")
	assert.Contains(t, prompt, "Simple CLI Application")
	assert.Contains(t, prompt, "Hello Command")
	assert.Contains(t, prompt, "Add a hello command that greets the user")
	assert.Contains(t, prompt, "The command should accept a --name flag")
	assert.Contains(t, prompt, "Goodbye Command")
	assert.Contains(t, prompt, "VALIDATION CONSTRAINTS:")
	assert.Contains(t, prompt, "main-file-exists (FileCheck): Main command file should exist")
	assert.Contains(t, prompt, "Details: cmd/app/main.go")
	assert.Contains(t, prompt, "INSTRUCTIONS:")
}

func TestClaudeAgent_parseGeneratedFiles(t *testing.T) {
	agent := NewClaudeAgent("test", ClaudeAgentConfig{})
	
	buildCtx := BuildContext{
		Intent: &src.Intent{
			Name: "cmd/app",
		},
		ProjectRoot: "/test/project",
	}

	tests := []struct {
		name     string
		output   string
		expected []string
	}{
		{
			name: "parse created files",
			output: `
Creating the hello command...
Created file: cmd/app/main.go
Created file: cmd/app/hello.go
Generated: cmd/app/goodbye.go
Writing to: cmd/app/config.go
Done!
`,
			expected: []string{
				"/test/project/cmd/app/main.go",
				"/test/project/cmd/app/hello.go",
				"/test/project/cmd/app/goodbye.go",
				"/test/project/cmd/app/config.go",
			},
		},
		{
			name: "no files parsed - use target",
			output: `
Just some output without file indicators
`,
			expected: []string{
				"/test/project/cmd/app",
			},
		},
		{
			name: "absolute paths",
			output: `
Created file: /absolute/path/file.go
Created file: relative/file.go
`,
			expected: []string{
				"/absolute/path/file.go",
				"/test/project/relative/file.go",
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			files := agent.parseGeneratedFiles(tt.output, buildCtx)
			assert.Equal(t, tt.expected, files)
		})
	}
}

func TestClaudeAgent_Build_MockExecution(t *testing.T) {
	// This test mocks the CLI execution for testing without actual Claude CLI
	agent := NewClaudeAgent("test", ClaudeAgentConfig{
		Timeout: 1 * time.Second,
		Retries: 1,
	})

	// Create a mock claude command that reads from stdin
	mockScript := `#!/bin/bash
# Read the prompt from stdin
cat > /dev/null
# Output some generated files
echo "Created file: cmd/app/main.go"
echo "Created file: cmd/app/hello.go"
`
	
	// Create temp directory for mock
	tmpDir := t.TempDir()
	mockPath := filepath.Join(tmpDir, "claude")
	err := os.WriteFile(mockPath, []byte(mockScript), 0755)
	require.NoError(t, err)

	// Add mock to PATH
	oldPath := os.Getenv("PATH")
	os.Setenv("PATH", fmt.Sprintf("%s%c%s", tmpDir, os.PathListSeparator, oldPath))
	defer os.Setenv("PATH", oldPath)

	buildCtx := BuildContext{
		Intent: &src.Intent{
			Name:         "cmd/app",
			Content:      "# Test App\n\nA simple test application.",
			Dependencies: []string{},
		},
		ProjectRoot:  tmpDir,
		GenerationID: "test-gen",
	}

	ctx := context.Background()
	files, err := agent.Build(ctx, buildCtx)
	
	require.NoError(t, err)
	assert.Len(t, files, 2)
	assert.Contains(t, files[0], "main.go")
	assert.Contains(t, files[1], "hello.go")
}

func TestClaudeAgent_Validate(t *testing.T) {
	agent := NewClaudeAgent("test", ClaudeAgentConfig{})

	// Create a mock claude command for validation that reads from stdin
	mockScript := `#!/bin/bash
# Read prompt from stdin and check for "file_exists"
if grep -q "file_exists"; then
  echo "PASS - The file exists as required"
else
  echo "FAIL - The validation check did not pass"
fi
`
	
	tmpDir := t.TempDir()
	mockPath := filepath.Join(tmpDir, "claude")
	err := os.WriteFile(mockPath, []byte(mockScript), 0755)
	require.NoError(t, err)

	oldPath := os.Getenv("PATH")
	os.Setenv("PATH", fmt.Sprintf("%s%c%s", tmpDir, os.PathListSeparator, oldPath))
	defer os.Setenv("PATH", oldPath)

	validation := &src.Validation{
		Name:        "file_exists",
		Type:        src.ValidationTypeFileCheck,
		Description: "Main file should exist",
		Parameters:  map[string]interface{}{},
	}

	ctx := context.Background()
	passed, output, err := agent.Validate(ctx, validation, []string{"main.go"})
	
	require.NoError(t, err)
	assert.True(t, passed)
	assert.Contains(t, output, "PASS")
}

func TestClaudeAgentFactory(t *testing.T) {
	factory := NewClaudeAgentFactory(ClaudeAgentConfig{
		Timeout: 5 * time.Minute,
		Retries: 3,
	})

	t.Run("create with defaults", func(t *testing.T) {
		config := src.Agent{
			Name: "test-agent",
			Type: "claude",
		}

		agent, err := factory.CreateAgent(config)
		require.NoError(t, err)
		require.NotNil(t, agent)

		claudeAgent, ok := agent.(*ClaudeAgent)
		require.True(t, ok)
		assert.Equal(t, "test-agent", claudeAgent.name)
		assert.Equal(t, 5*time.Minute, claudeAgent.timeout)
		assert.Equal(t, 3, claudeAgent.retries)
	})

	t.Run("create with overrides", func(t *testing.T) {
		config := src.Agent{
			Name: "custom-agent",
			Type: "claude",
			Config: map[string]interface{}{
				"timeout":    "10m",
				"retries":    float64(5),
				"rate_limit": "2s",
			},
		}

		agent, err := factory.CreateAgent(config)
		require.NoError(t, err)
		require.NotNil(t, agent)

		claudeAgent, ok := agent.(*ClaudeAgent)
		require.True(t, ok)
		assert.Equal(t, "custom-agent", claudeAgent.name)
		assert.Equal(t, 10*time.Minute, claudeAgent.timeout)
		assert.Equal(t, 5, claudeAgent.retries)
		assert.Equal(t, 2*time.Second, claudeAgent.rateLimit)
	})

	t.Run("supported types", func(t *testing.T) {
		types := factory.GetSupportedTypes()
		assert.Equal(t, []string{"claude"}, types)
	})
}

func TestClaudeAgent_executeClaudeCLI_Timeout(t *testing.T) {
	agent := NewClaudeAgent("test", ClaudeAgentConfig{
		Timeout: 100 * time.Millisecond,
	})

	// Create a mock that sleeps longer than timeout
	mockScript := `#!/bin/bash
sleep 2
echo "Should not see this"
`
	
	tmpDir := t.TempDir()
	mockPath := filepath.Join(tmpDir, "claude")
	err := os.WriteFile(mockPath, []byte(mockScript), 0755)
	require.NoError(t, err)

	oldPath := os.Getenv("PATH")
	os.Setenv("PATH", fmt.Sprintf("%s%c%s", tmpDir, os.PathListSeparator, oldPath))
	defer os.Setenv("PATH", oldPath)

	ctx := context.Background()
	_, err = agent.executeClaudeCLI(ctx, "test prompt", tmpDir)
	
	require.Error(t, err)
	assert.Contains(t, err.Error(), "timed out")
}

func TestClaudeAgent_Build_Retries(t *testing.T) {
	agent := NewClaudeAgent("test", ClaudeAgentConfig{
		Timeout:   1 * time.Second,
		Retries:   2,
		RateLimit: 10 * time.Millisecond,
	})

	// Create a mock that fails first time, succeeds second
	mockScript := fmt.Sprintf(`#!/bin/bash
ATTEMPTS_FILE="%s/attempts"
if [ -f "$ATTEMPTS_FILE" ]; then
  ATTEMPTS=$(cat "$ATTEMPTS_FILE")
else
  ATTEMPTS=0
fi
ATTEMPTS=$((ATTEMPTS + 1))
echo $ATTEMPTS > "$ATTEMPTS_FILE"

if [ $ATTEMPTS -lt 2 ]; then
  exit 1
fi
echo "Created file: success.go"
`, t.TempDir())
	
	tmpDir := t.TempDir()
	mockPath := filepath.Join(tmpDir, "claude")
	err := os.WriteFile(mockPath, []byte(mockScript), 0755)
	require.NoError(t, err)

	oldPath := os.Getenv("PATH")
	os.Setenv("PATH", fmt.Sprintf("%s%c%s", tmpDir, os.PathListSeparator, oldPath))
	defer os.Setenv("PATH", oldPath)

	buildCtx := BuildContext{
		Intent: &src.Intent{
			Name: "test",
		},
		ProjectRoot: tmpDir,
	}

	ctx := context.Background()
	files, err := agent.Build(ctx, buildCtx)
	
	require.NoError(t, err)
	assert.Contains(t, files[0], "success.go")
}