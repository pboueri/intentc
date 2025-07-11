package cmd

import (
	"context"
	"os"
	"testing"

	"github.com/pboueri/intentc/src/git"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func setupTestRepo(t *testing.T) (string, git.GitManager, func()) {
	// Create a temporary directory for testing
	tempDir, err := os.MkdirTemp("", "intentc-test-*")
	require.NoError(t, err)

	// Change to temp directory
	originalWd, err := os.Getwd()
	require.NoError(t, err)
	err = os.Chdir(tempDir)
	require.NoError(t, err)

	// Initialize as a git repo
	gitMgr := git.NewGitManager(tempDir)
	ctx := context.Background()
	err = gitMgr.Initialize(ctx, tempDir)
	require.NoError(t, err)

	// Create .intentc directory
	err = os.MkdirAll(".intentc", 0755)
	require.NoError(t, err)

	// Create config file
	configContent := `agent:
  provider: mock
`
	err = os.WriteFile(".intentc/config.yaml", []byte(configContent), 0644)
	require.NoError(t, err)

	// Create .gitignore to ignore state directory
	err = os.WriteFile(".gitignore", []byte(".intentc/state/\n"), 0644)
	require.NoError(t, err)

	// Create initial commit to avoid "no commits yet" error
	err = gitMgr.Add(ctx, []string{".intentc/config.yaml", ".gitignore"})
	require.NoError(t, err)
	err = gitMgr.Commit(ctx, "Initial commit")
	require.NoError(t, err)
	
	cleanup := func() {
		os.Chdir(originalWd)
		os.RemoveAll(tempDir)
	}
	
	return tempDir, gitMgr, cleanup
}

func TestCommitCommand(t *testing.T) {
	ctx := context.Background()

	t.Run("no staged files", func(t *testing.T) {
		_, _, cleanup := setupTestRepo(t)
		defer cleanup()
		
		// Execute commit command
		rootCmd.SetArgs([]string{"commit", "-m", "test commit"})
		err := rootCmd.Execute()
		assert.NoError(t, err)
	})

	t.Run("commit intent files", func(t *testing.T) {
		_, gitMgr, cleanup := setupTestRepo(t)
		defer cleanup()
		
		// Create an intent file
		err := os.MkdirAll("intent/test", 0755)
		require.NoError(t, err)
		err = os.WriteFile("intent/test/test.ic", []byte("# Test Intent"), 0644)
		require.NoError(t, err)

		// Stage the file
		err = gitMgr.Add(ctx, []string{"intent/test/test.ic"})
		require.NoError(t, err)

		// Execute commit command
		rootCmd.SetArgs([]string{"commit", "-m", "add test intent"})
		err = rootCmd.Execute()
		assert.NoError(t, err)

		// Verify commit was created
		commits, err := gitMgr.GetLog(ctx, 2)
		require.NoError(t, err)
		require.Len(t, commits, 2)
		assert.Equal(t, "intent: add test intent", commits[0].Message)
	})

	t.Run("commit generated files", func(t *testing.T) {
		_, gitMgr, cleanup := setupTestRepo(t)
		defer cleanup()
		
		// Create a generated file
		err := os.MkdirAll("src", 0755)
		require.NoError(t, err)
		err = os.WriteFile("src/main.go", []byte("package main"), 0644)
		require.NoError(t, err)

		// Stage the file
		err = gitMgr.Add(ctx, []string{"src/main.go"})
		require.NoError(t, err)

		// Check status before commit
		statusBefore, _ := gitMgr.GetStatus(ctx)
		t.Logf("Status before commit: staged=%v", statusBefore.StagedFiles)
		
		// Execute commit command
		rootCmd.SetArgs([]string{"commit", "-m", "generate main"})
		err = rootCmd.Execute()
		if err != nil {
			t.Logf("Commit error: %v", err)
		}
		assert.NoError(t, err)
		
		// Check status after commit
		statusAfter, _ := gitMgr.GetStatus(ctx)
		t.Logf("Status after commit: clean=%v, staged=%v, modified=%v, untracked=%v", 
			statusAfter.Clean, statusAfter.StagedFiles, statusAfter.ModifiedFiles, statusAfter.UntrackedFiles)

		// Verify commit was created (skip the initial commit)
		commits, err := gitMgr.GetLog(ctx, 2)
		require.NoError(t, err)
		require.Len(t, commits, 2)
		// The most recent commit should be our new one
		assert.Equal(t, "generated: generate main", commits[0].Message)
		assert.Equal(t, "Initial commit", commits[1].Message)
	})

	t.Run("commit mixed files", func(t *testing.T) {
		_, gitMgr, cleanup := setupTestRepo(t)
		defer cleanup()
		
		// Create intent and generated directories first
		err := os.MkdirAll("intent/test", 0755)
		require.NoError(t, err)
		err = os.MkdirAll("src", 0755)
		require.NoError(t, err)
		
		// Create both intent and generated files
		err = os.WriteFile("intent/test/another.ic", []byte("# Another Intent"), 0644)
		require.NoError(t, err)
		err = os.WriteFile("src/another.go", []byte("package another"), 0644)
		require.NoError(t, err)

		// Stage both files
		err = gitMgr.Add(ctx, []string{"intent/test/another.ic", "src/another.go"})
		require.NoError(t, err)

		// Execute commit command
		rootCmd.SetArgs([]string{"commit", "-m", "mixed changes"})
		err = rootCmd.Execute()
		assert.NoError(t, err)

		// Get the most recent commits
		commits, err := gitMgr.GetLog(ctx, 2)
		require.NoError(t, err)
		require.Len(t, commits, 2)
		
		// When files are already staged together, git commits them all together
		// The commit should have the intent prefix since intent files are processed first
		assert.Equal(t, "intent: mixed changes", commits[0].Message)
	})

	t.Run("commit with --all flag", func(t *testing.T) {
		_, gitMgr, cleanup := setupTestRepo(t)
		defer cleanup()
		
		// Create untracked files
		err := os.WriteFile("new.txt", []byte("new file"), 0644)
		require.NoError(t, err)

		// Execute commit command with --all flag
		rootCmd.SetArgs([]string{"commit", "-m", "all changes", "--all"})
		err = rootCmd.Execute()
		assert.NoError(t, err)

		// Verify commit was created
		commits, err := gitMgr.GetLog(ctx, 2)
		require.NoError(t, err)
		require.Len(t, commits, 2)
		assert.Equal(t, "generated: all changes", commits[0].Message)
	})
}

func TestIsIntentFile(t *testing.T) {
	tests := []struct {
		name     string
		path     string
		expected bool
	}{
		{"intent file", "intent/test/test.ic", true},
		{"validation file", "intent/test/test.icv", true},
		{"go file", "src/main.go", false},
		{"text file", "README.txt", false},
		{"no extension", "Makefile", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := isIntentFile(tt.path)
			assert.Equal(t, tt.expected, result)
		})
	}
}

func TestFindTargetsFromFiles(t *testing.T) {
	tests := []struct {
		name     string
		files    []string
		expected []string
	}{
		{
			name:     "files in target directories",
			files:    []string{"api/main.go", "api/handler.go", "web/index.html"},
			expected: []string{"api", "web"},
		},
		{
			name:     "files in root",
			files:    []string{"main.go", "README.md"},
			expected: []string{},
		},
		{
			name:     "nested files",
			files:    []string{"api/v1/handler.go", "api/v2/handler.go"},
			expected: []string{"api"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := findTargetsFromFiles(tt.files)
			// Sort for consistent comparison
			assert.ElementsMatch(t, tt.expected, result)
		})
	}
}