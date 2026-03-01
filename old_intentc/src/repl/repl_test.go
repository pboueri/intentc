package repl

import (
	"bufio"
	"bytes"
	"context"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/agent"
	"github.com/pboueri/intentc/src/config"
	"github.com/pboueri/intentc/src/git"
	"github.com/pboueri/intentc/src/state"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// mockReader implements a reader that provides predefined input
type mockReader struct {
	inputs []string
	index  int
}

func newMockReader(inputs ...string) *mockReader {
	return &mockReader{
		inputs: inputs,
		index:  0,
	}
}

func (m *mockReader) Read(p []byte) (n int, err error) {
	if m.index >= len(m.inputs) {
		return 0, io.EOF
	}
	input := m.inputs[m.index] + "\n"
	m.index++
	n = copy(p, input)
	return n, nil
}

func TestREPLCommands(t *testing.T) {
	// Setup test environment
	tempDir, err := os.MkdirTemp("", "intentc-repl-test-*")
	require.NoError(t, err)
	defer os.RemoveAll(tempDir)

	// Change to temp directory
	originalWd, err := os.Getwd()
	require.NoError(t, err)
	defer os.Chdir(originalWd)
	err = os.Chdir(tempDir)
	require.NoError(t, err)

	// Initialize git
	gitMgr := git.NewGitManager(tempDir)
	ctx := context.Background()
	err = gitMgr.Initialize(ctx, tempDir)
	require.NoError(t, err)

	// Create test structure
	err = os.MkdirAll("intent/test", 0755)
	require.NoError(t, err)
	
	// Create test intent
	intentContent := `# Test Feature
This is a test feature.

## Dependencies
Depends On: 

## Intent
Create a simple test implementation.`
	err = os.WriteFile("intent/test/test.ic", []byte(intentContent), 0644)
	require.NoError(t, err)

	// Create config
	cfg := &config.Config{
		Agent: config.AgentConfig{
			Provider: "mock",
		},
	}

	// Create mock agent
	mockAgent := &agent.MockAgent{
		BuildFunc: func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
			// Create a test file
			testFile := filepath.Join(tempDir, "test.go")
			err := os.WriteFile(testFile, []byte("package test\n\nfunc Test() {}\n"), 0644)
			if err != nil {
				return nil, err
			}
			return []string{testFile}, nil
		},
		RefineFunc: func(ctx context.Context, target *src.Target, prompt string) error {
			// Modify the test file
			testFile := filepath.Join(tempDir, "test.go")
			content := "package test\n\nfunc Test() {\n\t// Refined\n}\n"
			return os.WriteFile(testFile, []byte(content), 0644)
		},
	}

	// Create state manager
	stateMgr := state.NewGitStateManager(gitMgr, tempDir)
	err = stateMgr.Initialize(ctx)
	require.NoError(t, err)

	// Create target
	target := &src.Target{
		Name: "test",
		Intent: &src.Intent{
			Name:    "test",
			Content: intentContent,
		},
		Validations: []*src.ValidationFile{},
	}

	// Save initial build result
	buildResult := &src.BuildResult{
		Target:       "test",
		GenerationID: "test-gen-1",
		Files:        []string{filepath.Join(tempDir, "test.go")},
		Success:      true,
		GeneratedAt:  time.Now(),
	}
	err = stateMgr.SaveBuildResult(ctx, buildResult)
	require.NoError(t, err)

	// Create initial test file
	err = os.WriteFile("test.go", []byte("package test\n\nfunc Test() {}\n"), 0644)
	require.NoError(t, err)

	t.Run("help command", func(t *testing.T) {
		output := &bytes.Buffer{}
		r := &REPL{
			config:       cfg,
			agent:        mockAgent,
			gitManager:   gitMgr,
			stateManager: stateMgr,
			target:       target,
			context:      NewReplContext(target, tempDir),
			reader:       bufio.NewReader(strings.NewReader("help\nexit\n")),
			writer:       output,
			projectRoot:  tempDir,
		}

		err := r.Run(ctx)
		assert.NoError(t, err)
		assert.Contains(t, output.String(), "Available commands:")
	})

	t.Run("show command", func(t *testing.T) {
		output := &bytes.Buffer{}
		r := &REPL{
			config:       cfg,
			agent:        mockAgent,
			gitManager:   gitMgr,
			stateManager: stateMgr,
			target:       target,
			context:      NewReplContext(target, tempDir),
			reader:       bufio.NewReader(strings.NewReader("show\nshow test.go\nexit\n")),
			writer:       output,
			projectRoot:  tempDir,
		}

		// Load initial state
		err := r.loadInitialState(ctx)
		require.NoError(t, err)

		err = r.Run(ctx)
		assert.NoError(t, err)
		assert.Contains(t, output.String(), "Generated files:")
		assert.Contains(t, output.String(), "test.go")
		assert.Contains(t, output.String(), "package test")
	})

	t.Run("refine command", func(t *testing.T) {
		output := &bytes.Buffer{}
		r := &REPL{
			config:       cfg,
			agent:        mockAgent,
			gitManager:   gitMgr,
			stateManager: stateMgr,
			target:       target,
			context:      NewReplContext(target, tempDir),
			reader:       bufio.NewReader(strings.NewReader("refine add comments\nexit\n")),
			writer:       output,
			projectRoot:  tempDir,
		}

		err := r.Run(ctx)
		assert.NoError(t, err)
		assert.Contains(t, output.String(), "Refining with agent...")
		assert.Contains(t, output.String(), "Refinement complete.")

		// Check that file was modified
		content, err := os.ReadFile("test.go")
		require.NoError(t, err)
		assert.Contains(t, string(content), "// Refined")
	})

	t.Run("history command", func(t *testing.T) {
		output := &bytes.Buffer{}
		r := &REPL{
			config:       cfg,
			agent:        mockAgent,
			gitManager:   gitMgr,
			stateManager: stateMgr,
			target:       target,
			context:      NewReplContext(target, tempDir),
			reader:       bufio.NewReader(strings.NewReader("refine test\nhistory\nexit\n")),
			writer:       output,
			projectRoot:  tempDir,
		}

		err := r.Run(ctx)
		assert.NoError(t, err)
		assert.Contains(t, output.String(), "Refinement History:")
		assert.Contains(t, output.String(), "Prompt: test")
	})

	t.Run("invalid command", func(t *testing.T) {
		output := &bytes.Buffer{}
		r := &REPL{
			config:       cfg,
			agent:        mockAgent,
			gitManager:   gitMgr,
			stateManager: stateMgr,
			target:       target,
			context:      NewReplContext(target, tempDir),
			reader:       bufio.NewReader(strings.NewReader("invalid\nexit\n")),
			writer:       output,
			projectRoot:  tempDir,
		}

		err := r.Run(ctx)
		assert.NoError(t, err)
		assert.Contains(t, output.String(), "unknown command: invalid")
	})
}

func TestReplContext(t *testing.T) {
	target := &src.Target{
		Name: "test",
		Intent: &src.Intent{
			Name:    "test",
			Content: "Test intent content",
		},
	}

	ctx := NewReplContext(target, "/test")

	t.Run("generated files", func(t *testing.T) {
		assert.Empty(t, ctx.GetGeneratedFiles())

		ctx.AddGeneratedFile("file1.go")
		ctx.AddGeneratedFile("file2.go")
		ctx.AddGeneratedFile("file1.go") // duplicate

		files := ctx.GetGeneratedFiles()
		assert.Len(t, files, 2)
		assert.Contains(t, files, "file1.go")
		assert.Contains(t, files, "file2.go")
	})

	t.Run("file contexts", func(t *testing.T) {
		ctx.AddFileContext("main.go", "package main")
		assert.Contains(t, ctx.fileContexts, "main.go")

		ctx.ClearFileContexts()
		assert.Empty(t, ctx.fileContexts)
	})

	t.Run("refinement history", func(t *testing.T) {
		assert.Empty(t, ctx.GetHistory())

		ctx.AddRefinement("test prompt", "")
		ctx.UpdateRefinementResponse("test prompt", "test response")

		history := ctx.GetHistory()
		assert.Len(t, history, 1)
		assert.Equal(t, "test prompt", history[0].Prompt)
		assert.Equal(t, "test response", history[0].Response)
	})

	t.Run("build refinement prompt", func(t *testing.T) {
		ctx.AddFileContext("test.go", "package test")
		ctx.AddRefinement("previous refinement", "done")

		prompt := ctx.BuildRefinementPrompt("new refinement")

		assert.Contains(t, prompt, "Target: test")
		assert.Contains(t, prompt, "Test intent content")
		assert.Contains(t, prompt, "test.go")
		assert.Contains(t, prompt, "package test")
		assert.Contains(t, prompt, "previous refinement")
		assert.Contains(t, prompt, "new refinement")
	})
}

func TestReplCommandParsing(t *testing.T) {
	tests := []struct {
		name        string
		input       string
		wantCommand string
		wantArgs    []string
	}{
		{
			name:        "simple command",
			input:       "help",
			wantCommand: "help",
			wantArgs:    []string{},
		},
		{
			name:        "command with args",
			input:       "show test.go",
			wantCommand: "show",
			wantArgs:    []string{"test.go"},
		},
		{
			name:        "command with multiple args",
			input:       "refine add more comments please",
			wantCommand: "refine",
			wantArgs:    []string{"add", "more", "comments", "please"},
		},
		{
			name:        "command with extra spaces",
			input:       "  commit   test message  ",
			wantCommand: "commit",
			wantArgs:    []string{"test", "message"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			input := strings.TrimSpace(tt.input)
			parts := strings.Fields(input)
			
			if len(parts) > 0 {
				assert.Equal(t, tt.wantCommand, parts[0])
				assert.Equal(t, tt.wantArgs, parts[1:])
			}
		})
	}
}