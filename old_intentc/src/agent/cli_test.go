package agent

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/git"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"
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

	// Create build directory
	buildDir := filepath.Join(tmpDir, "build-test")
	err = os.MkdirAll(buildDir, 0755)
	require.NoError(t, err)

	// Create build context
	buildCtx := BuildContext{
		Intent: &src.Intent{
			Name:    "test-target",
			Content: "Create a simple Go program",
		},
		Validations:  []*src.ValidationFile{},
		ProjectRoot:  tmpDir,
		GenerationID: "test-gen-123",
		BuildName:    "test",
		BuildPath:    buildDir,
	}

	// Execute build
	ctx := context.Background()
	files, err := agent.Build(ctx, buildCtx)
	require.NoError(t, err)

	// Verify results
	assert.Len(t, files, 1)
	assert.Contains(t, files[0], "test.go")

	// Verify file was created in build directory
	testFile := filepath.Join(buildDir, "test.go")
	assert.FileExists(t, testFile)
}

func TestCLIAgentRetries(t *testing.T) {
	// Create a temporary directory
	tmpDir, err := os.MkdirTemp("", "cli-agent-retry-test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)
	
	// Create attempt counter file
	attemptFile := filepath.Join(tmpDir, "attempt")
	err = os.WriteFile(attemptFile, []byte("0"), 0644)
	require.NoError(t, err)
	
	// Create a script that fails first time, succeeds second time
	scriptPath := filepath.Join(tmpDir, "retry-test.sh")
	scriptContent := `#!/bin/bash
# Read and increment attempt counter
attempt=$(cat attempt)
attempt=$((attempt + 1))
echo $attempt > attempt

echo "Attempt $attempt at $(date)" >&2

if [ $attempt -eq 1 ]; then
    echo "First attempt - failing" >&2
    exit 1
elif [ $attempt -eq 2 ]; then
    echo "Second attempt - success" >&2
    echo "Created success.txt"
    echo "Success!" > success.txt
    exit 0
else
    echo "Unexpected attempt number: $attempt" >&2
    exit 1
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
		BuildPath:    tmpDir, // For tests, use tmpDir as build path
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
		BuildPath:    tmpDir, // For tests, use tmpDir as build path
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
			files := agent.parseGeneratedFiles(tt.output, "/tmp/test")
			assert.Equal(t, tt.expected, files)
		})
	}
}

// mockGitManager for testing
type mockGitManager struct {
	mock.Mock
}

func (m *mockGitManager) Initialize(ctx context.Context, path string) error {
	args := m.Called(ctx, path)
	return args.Error(0)
}

func (m *mockGitManager) IsGitRepo(ctx context.Context, path string) (bool, error) {
	args := m.Called(ctx, path)
	return args.Bool(0), args.Error(1)
}

func (m *mockGitManager) Add(ctx context.Context, files []string) error {
	args := m.Called(ctx, files)
	return args.Error(0)
}

func (m *mockGitManager) Commit(ctx context.Context, message string) error {
	args := m.Called(ctx, message)
	return args.Error(0)
}

func (m *mockGitManager) GetCurrentBranch(ctx context.Context) (string, error) {
	args := m.Called(ctx)
	return args.String(0), args.Error(1)
}

func (m *mockGitManager) GetCommitHash(ctx context.Context) (string, error) {
	args := m.Called(ctx)
	return args.String(0), args.Error(1)
}

func (m *mockGitManager) CheckoutCommit(ctx context.Context, commitHash string) error {
	args := m.Called(ctx, commitHash)
	return args.Error(0)
}

func (m *mockGitManager) CreateBranch(ctx context.Context, branchName string) error {
	args := m.Called(ctx, branchName)
	return args.Error(0)
}

func (m *mockGitManager) GetStatus(ctx context.Context) (*git.GitStatus, error) {
	args := m.Called(ctx)
	if args.Get(0) == nil {
		return nil, args.Error(1)
	}
	return args.Get(0).(*git.GitStatus), args.Error(1)
}

func (m *mockGitManager) GetLog(ctx context.Context, limit int) ([]*git.GitCommit, error) {
	args := m.Called(ctx, limit)
	if args.Get(0) == nil {
		return nil, args.Error(1)
	}
	return args.Get(0).([]*git.GitCommit), args.Error(1)
}

func TestCLIAgentDetectGeneratedFiles(t *testing.T) {
	agent := NewCLIAgent(CLIAgentConfig{
		Name:    "test-agent",
		Command: "echo",
	})

	// Create mock git status before
	beforeStatus := &git.GitStatus{
		ModifiedFiles:  []string{"existing.go"},
		UntrackedFiles: []string{},
	}

	// Create mock git status after
	afterStatus := &git.GitStatus{
		ModifiedFiles:  []string{"existing.go", "new-modified.go"},
		UntrackedFiles: []string{"new-file1.go", "subdir/new-file2.go"},
	}

	// Create mock git manager
	mockGit := &mockGitManager{}
	mockGit.On("GetStatus", mock.Anything).Return(afterStatus, nil)

	buildCtx := BuildContext{
		ProjectRoot: "/test/project",
		BuildPath:   "/test/project/build",
		GitManager:  mockGit,
	}

	files, err := agent.detectGeneratedFiles(context.Background(), buildCtx, beforeStatus)
	require.NoError(t, err)

	expected := []string{
		"/test/project/new-file1.go",
		"/test/project/subdir/new-file2.go",
		"/test/project/new-modified.go",
	}

	assert.ElementsMatch(t, expected, files)
}

func TestCLIAgentBuildWithGitDetection(t *testing.T) {
	// Create a script that doesn't output file paths
	tmpDir, err := os.MkdirTemp("", "cli-agent-git-test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	scriptPath := filepath.Join(tmpDir, "build.sh")
	scriptContent := `#!/bin/bash
echo "Building project..."
echo "Done building."
`
	err = os.WriteFile(scriptPath, []byte(scriptContent), 0755)
	require.NoError(t, err)

	// Create CLI agent
	config := CLIAgentConfig{
		Name:       "build-agent",
		Command:    "bash",
		Args:       []string{scriptPath},
		Timeout:    5 * time.Second,
		WorkingDir: tmpDir,
	}
	agent := NewCLIAgent(config)

	// Mock git statuses
	beforeStatus := &git.GitStatus{
		ModifiedFiles:  []string{},
		UntrackedFiles: []string{},
	}

	afterStatus := &git.GitStatus{
		ModifiedFiles:  []string{},
		UntrackedFiles: []string{"output/result.txt", "output/data.json"},
	}

	// Create mock git manager
	mockGit := &mockGitManager{}
	mockGit.On("GetStatus", mock.Anything).Return(beforeStatus, nil).Once()
	mockGit.On("GetStatus", mock.Anything).Return(afterStatus, nil).Once()

	// Create build context
	buildCtx := BuildContext{
		Intent: &src.Intent{
			Name:    "test-feature",
			Content: "Build something",
		},
		ProjectRoot:  tmpDir,
		GenerationID: "gen-123",
		GitManager:   mockGit,
		BuildPath:    tmpDir, // For tests, use tmpDir as build path
	}

	// Execute build
	ctx := context.Background()
	files, err := agent.Build(ctx, buildCtx)
	require.NoError(t, err)

	// Should detect files via git
	expected := []string{
		filepath.Join(tmpDir, "output/result.txt"),
		filepath.Join(tmpDir, "output/data.json"),
	}
	assert.ElementsMatch(t, expected, files)
	mockGit.AssertExpectations(t)
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

// TestCLIAgentBuildDirectoryIsolation tests that the agent executes in the build directory
func TestCLIAgentBuildDirectoryIsolation(t *testing.T) {
	// Create a temporary directory for testing
	tmpDir, err := os.MkdirTemp("", "cli-agent-build-isolation")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	// Create build directory
	buildDir := filepath.Join(tmpDir, "build-isolated")
	err = os.MkdirAll(buildDir, 0755)
	require.NoError(t, err)

	// Create a test script that prints the working directory and creates a file
	scriptPath := filepath.Join(tmpDir, "check-pwd.sh")
	scriptContent := `#!/bin/bash
echo "Working directory: $(pwd)"
echo "Creating file in current directory..."
echo "test content" > generated.txt
echo "Generated file: generated.txt"
`
	err = os.WriteFile(scriptPath, []byte(scriptContent), 0755)
	require.NoError(t, err)

	// Create CLI agent
	config := CLIAgentConfig{
		Name:    "test-agent",
		Command: "bash",
		Args:    []string{scriptPath},
		Timeout: 5 * time.Second,
	}
	agent := NewCLIAgent(config)

	// Create build context with build directory
	buildCtx := BuildContext{
		Intent: &src.Intent{
			Name:    "test-isolation",
			Content: "Test working directory isolation",
		},
		Validations:  []*src.ValidationFile{},
		ProjectRoot:  tmpDir,
		GenerationID: "test-gen-456",
		BuildName:    "isolated",
		BuildPath:    buildDir,
	}

	// Execute build
	ctx := context.Background()
	files, err := agent.Build(ctx, buildCtx)
	require.NoError(t, err)

	// Verify that working directory was set to build directory
	assert.Equal(t, buildDir, agent.workingDir)

	// Verify file was detected
	assert.Len(t, files, 1)
	assert.Contains(t, files[0], "generated.txt")

	// Verify the file was created in the build directory
	generatedFile := filepath.Join(buildDir, "generated.txt")
	_, err = os.Stat(generatedFile)
	assert.NoError(t, err, "File should exist in build directory")

	// Verify the file was NOT created in the project root
	rootFile := filepath.Join(tmpDir, "generated.txt")
	_, err = os.Stat(rootFile)
	assert.True(t, os.IsNotExist(err), "File should NOT exist in project root")
}

// TestCLIAgentMultipleBuildDirectories tests multiple builds with different directories
func TestCLIAgentMultipleBuildDirectories(t *testing.T) {
	// Create a temporary directory for testing
	tmpDir, err := os.MkdirTemp("", "cli-agent-multi-build")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	// Create a test script that creates a file with build name content
	scriptPath := filepath.Join(tmpDir, "create-build-file.sh")
	scriptContent := `#!/bin/bash
# Extract build name from working directory
BUILD_NAME=$(basename "$PWD")
echo "Creating file for build: $BUILD_NAME"
echo "$BUILD_NAME" > output.txt
echo "Generated file: output.txt"
`
	err = os.WriteFile(scriptPath, []byte(scriptContent), 0755)
	require.NoError(t, err)

	// Test with multiple build names
	buildNames := []string{"dev", "staging", "prod"}
	
	for _, buildName := range buildNames {
		// Create build directory
		buildDir := filepath.Join(tmpDir, "build-"+buildName)
		err = os.MkdirAll(buildDir, 0755)
		require.NoError(t, err)

		// Create CLI agent
		config := CLIAgentConfig{
			Name:    "test-agent",
			Command: "bash",
			Args:    []string{scriptPath},
			Timeout: 5 * time.Second,
		}
		agent := NewCLIAgent(config)

		// Create build context
		buildCtx := BuildContext{
			Intent: &src.Intent{
				Name:    buildName + "-target",
				Content: buildName, // Pass build name as content
			},
			Validations:  []*src.ValidationFile{},
			ProjectRoot:  tmpDir,
			GenerationID: "gen-" + buildName,
			BuildName:    buildName,
			BuildPath:    buildDir,
		}

		// Execute build
		ctx := context.Background()
		_, err = agent.Build(ctx, buildCtx)
		require.NoError(t, err)

		// Verify file was created in the correct build directory
		outputFile := filepath.Join(buildDir, "output.txt")
		content, err := os.ReadFile(outputFile)
		require.NoError(t, err)
		assert.Equal(t, "build-"+buildName+"\n", string(content))

		// Verify files in other build directories are not affected
		for _, otherBuild := range buildNames {
			if otherBuild != buildName {
				otherFile := filepath.Join(tmpDir, "build-"+otherBuild, "output.txt")
				if _, err := os.Stat(otherFile); err == nil {
					// If file exists, verify it has the correct content
					otherContent, _ := os.ReadFile(otherFile)
					assert.Equal(t, "build-"+otherBuild+"\n", string(otherContent))
				}
			}
		}
	}
}