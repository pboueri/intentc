package cmd

import (
	"os"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestRefineCommand(t *testing.T) {
	// Create a temporary directory for testing
	tempDir, err := os.MkdirTemp("", "intentc-refine-test-*")
	require.NoError(t, err)
	defer os.RemoveAll(tempDir)

	// Change to temp directory
	originalWd, err := os.Getwd()
	require.NoError(t, err)
	defer os.Chdir(originalWd)
	err = os.Chdir(tempDir)
	require.NoError(t, err)

	// Initialize project structure
	err = os.MkdirAll(".intentc", 0755)
	require.NoError(t, err)

	// Create config
	configContent := `agent:
  provider: mock
`
	err = os.WriteFile(".intentc/config.yaml", []byte(configContent), 0644)
	require.NoError(t, err)

	// Create intent structure
	err = os.MkdirAll("intent/api", 0755)
	require.NoError(t, err)

	intentContent := `# API
This is the API feature.

## Dependencies
Depends On: 

## Intent
Create a REST API.`
	err = os.WriteFile("intent/api/api.ic", []byte(intentContent), 0644)
	require.NoError(t, err)

	t.Run("refine without target", func(t *testing.T) {
		rootCmd.SetArgs([]string{"refine"})
		err = rootCmd.Execute()
		assert.Error(t, err)
		assert.Contains(t, err.Error(), "target required")
	})

	t.Run("refine with invalid target", func(t *testing.T) {
		rootCmd.SetArgs([]string{"refine", "nonexistent"})
		err = rootCmd.Execute()
		assert.Error(t, err)
		assert.Contains(t, err.Error(), "failed to parse target intent")
	})

	// Note: Full integration test with REPL would require more setup
	// including git initialization, state management, etc.
	// The REPL itself is tested in repl_test.go
}

func TestRefineCommandHelp(t *testing.T) {
	// Test that help text is properly displayed
	rootCmd.SetArgs([]string{"refine", "--help"})
	err := rootCmd.Execute()
	assert.NoError(t, err)
}