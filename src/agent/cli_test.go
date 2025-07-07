package agent

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/pboueri/intentc/src"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestNewCLIAgent(t *testing.T) {
	config := CLIAgentConfig{
		Name:      "test-agent",
		Command:   "echo",
		Args:      []string{"test"},
		Timeout:   30 * time.Second,
		Retries:   2,
		RateLimit: 500 * time.Millisecond,
	}

	agent := NewCLIAgent(config)

	assert.Equal(t, "test-agent", agent.GetName())
	assert.Equal(t, "cli", agent.GetType())
	assert.Equal(t, "echo", agent.command)
	assert.Equal(t, []string{"test"}, agent.args)
	assert.Equal(t, 30*time.Second, agent.timeout)
	assert.Equal(t, 2, agent.retries)
	assert.Equal(t, 500*time.Millisecond, agent.rateLimit)
}

func TestCLIAgentDefaults(t *testing.T) {
	config := CLIAgentConfig{
		Name:    "test-agent",
		Command: "echo",
	}

	agent := NewCLIAgent(config)

	assert.Equal(t, 5*time.Minute, agent.timeout)
	assert.Equal(t, 3, agent.retries)
	assert.Equal(t, time.Second, agent.rateLimit)
}

func TestCLIAgentBuild(t *testing.T) {
	// Create a temporary directory for testing
	tmpDir, err := os.MkdirTemp("", "cli-agent-test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	// Create a test script that generates a file
	scriptPath := filepath.Join(tmpDir, "test-agent.sh")
	scriptContent := `#!/bin/bash
echo "Generated file: test.go"
cat > test.go << 'EOF'
package main

func main() {
    println("Hello from generated code")
}
EOF
`
	err = os.WriteFile(scriptPath, []byte(scriptContent), 0755)
	require.NoError(t, err)

	// Create CLI agent
	config := CLIAgentConfig{
		Name:       "test-agent",
		Command:    "bash",
		Args:       []string{scriptPath},
		Timeout:    10 * time.Second,
		WorkingDir: tmpDir,
	}
	agent := NewCLIAgent(config)

	// Create build context
	buildCtx := BuildContext{
		Intent: &src.Intent{
			Name:    "test-target",
			Content: "Create a simple Go program",
		},
		Validations:  []*src.ValidationFile{},
		ProjectRoot:  tmpDir,
		GenerationID: "test-gen-123",
	}

	// Execute build
	ctx := context.Background()
	files, err := agent.Build(ctx, buildCtx)
	require.NoError(t, err)

	// Verify results
	assert.Len(t, files, 1)
	assert.Contains(t, files[0], "test.go")

	// Verify file was created
	testFile := filepath.Join(tmpDir, "test.go")
	assert.FileExists(t, testFile)
}

func TestCLIAgentRetries(t *testing.T) {
	// Create a temporary directory
	tmpDir, err := os.MkdirTemp("", "cli-agent-retry-test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	// Create a script that fails first time, succeeds second time
	scriptPath := filepath.Join(tmpDir, "retry-test.sh")
	counterFile := filepath.Join(tmpDir, "counter")
	scriptContent := `#!/bin/bash
counter=0
if [ -f "` + counterFile + `" ]; then
    counter=$(cat "` + counterFile + `")
fi
counter=$((counter + 1))
echo $counter > "` + counterFile + `"

if [ $counter -eq 1 ]; then
    echo "First attempt - failing" >&2
    exit 1
else
    echo "Second attempt - success"
    echo "Generated file: success.txt"
    echo "Success!" > success.txt
fi
`
	err = os.WriteFile(scriptPath, []byte(scriptContent), 0755)
	require.NoError(t, err)

	// Create CLI agent with retries
	config := CLIAgentConfig{
		Name:       "retry-agent",
		Command:    "bash",
		Args:       []string{scriptPath},
		Timeout:    5 * time.Second,
		Retries:    2,
		RateLimit:  100 * time.Millisecond,
		WorkingDir: tmpDir,
	}
	agent := NewCLIAgent(config)

	// Create build context
	buildCtx := BuildContext{
		Intent: &src.Intent{
			Name:    "test-target",
			Content: "Test retry mechanism",
		},
		ProjectRoot:  tmpDir,
		GenerationID: "test-gen-456",
	}

	// Execute build - should succeed on second attempt
	ctx := context.Background()
	files, err := agent.Build(ctx, buildCtx)
	require.NoError(t, err)

	// Verify results
	assert.Contains(t, files[0], "success.txt")
	assert.FileExists(t, filepath.Join(tmpDir, "success.txt"))
}

func TestCLIAgentTimeout(t *testing.T) {
	// Create a script that sleeps longer than timeout
	tmpDir, err := os.MkdirTemp("", "cli-agent-timeout-test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	scriptPath := filepath.Join(tmpDir, "timeout-test.sh")
	scriptContent := `#!/bin/bash
echo "Starting long operation..."
sleep 10
echo "This should not be reached"
`
	err = os.WriteFile(scriptPath, []byte(scriptContent), 0755)
	require.NoError(t, err)

	// Create CLI agent with short timeout
	config := CLIAgentConfig{
		Name:       "timeout-agent",
		Command:    "bash",
		Args:       []string{scriptPath},
		Timeout:    1 * time.Second,
		Retries:    1,
		WorkingDir: tmpDir,
	}
	agent := NewCLIAgent(config)

	// Create build context
	buildCtx := BuildContext{
		Intent: &src.Intent{
			Name:    "test-target",
			Content: "Test timeout",
		},
		ProjectRoot:  tmpDir,
		GenerationID: "test-gen-789",
	}

	// Execute build - should timeout
	ctx := context.Background()
	_, err = agent.Build(ctx, buildCtx)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "timed out")
}

func TestCLIAgentParseGeneratedFiles(t *testing.T) {
	agent := NewCLIAgent(CLIAgentConfig{
		Name:       "test-agent",
		Command:    "echo",
		WorkingDir: "/tmp/test",
	})

	tests := []struct {
		name     string
		output   string
		expected []string
	}{
		{
			name: "Created pattern",
			output: `Starting generation...
Created src/main.go
Created src/utils.go
Done.`,
			expected: []string{"/tmp/test/src/main.go", "/tmp/test/src/utils.go"},
		},
		{
			name: "Generated pattern",
			output: `Processing...
Generated: lib/helper.js
Generated: lib/index.js`,
			expected: []string{"/tmp/test/lib/helper.js", "/tmp/test/lib/index.js"},
		},
		{
			name: "Mixed patterns",
			output: `Building project...
Created config.yaml
Wrote test/test.go
Modified README.md
Generated: docs/api.md`,
			expected: []string{
				"/tmp/test/config.yaml",
				"/tmp/test/test/test.go",
				"/tmp/test/README.md",
				"/tmp/test/docs/api.md",
			},
		},
		{
			name:     "No files",
			output:   "Just some random output without file mentions",
			expected: []string{},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			files := agent.parseGeneratedFiles(tt.output)
			assert.Equal(t, tt.expected, files)
		})
	}
}

func TestCLIAgentValidate(t *testing.T) {
	// Create a validation script
	tmpDir, err := os.MkdirTemp("", "cli-agent-validate-test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	scriptPath := filepath.Join(tmpDir, "validate.sh")
	scriptContent := `#!/bin/bash
# Read all input
prompt=$(cat)
if echo "$prompt" | grep -q "file_exists"; then
    echo "PASS: File exists validation passed"
else
    echo "FAIL: Unknown validation type"
fi
`
	err = os.WriteFile(scriptPath, []byte(scriptContent), 0755)
	require.NoError(t, err)

	// Create CLI agent
	config := CLIAgentConfig{
		Name:       "validate-agent",
		Command:    "bash",
		Args:       []string{scriptPath},
		Timeout:    5 * time.Second,
		WorkingDir: tmpDir,
	}
	agent := NewCLIAgent(config)

	// Test validation
	validation := &src.Validation{
		Name:        "file_exists",
		Type:        "FileCheck",
		Description: "Check if file exists",
	}

	ctx := context.Background()
	passed, explanation, err := agent.Validate(ctx, validation, []string{"test.go"})
	require.NoError(t, err)
	assert.True(t, passed)
	assert.Contains(t, explanation, "PASS")
}