package cmd

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/pboueri/intentc/src/git"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestInitThenBuild(t *testing.T) {
	// Create a temporary directory for the test
	tmpDir, err := os.MkdirTemp("", "init-build-test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	// Save original working directory
	originalWD, err := os.Getwd()
	require.NoError(t, err)
	defer os.Chdir(originalWD)

	// Change to temp directory
	err = os.Chdir(tmpDir)
	require.NoError(t, err)

	// Initialize git repository
	gitMgr := git.NewGitManager(tmpDir)
	ctx := context.Background()
	err = gitMgr.Initialize(ctx, tmpDir)
	require.NoError(t, err)

	// Run init command
	err = runInit(nil, []string{})
	require.NoError(t, err)

	// Verify init created the correct structure
	// Check .intentc is a directory
	intentcInfo, err := os.Stat(".intentc")
	require.NoError(t, err)
	assert.True(t, intentcInfo.IsDir(), ".intentc should be a directory")

	// Check config.yaml exists
	configPath := filepath.Join(".intentc", "config.yaml")
	_, err = os.Stat(configPath)
	assert.NoError(t, err)

	// Check intent directory exists
	intentDir := "intent"
	_, err = os.Stat(intentDir)
	assert.NoError(t, err)

	// Check example feature exists
	exampleFeature := filepath.Join(intentDir, "example_feature")
	_, err = os.Stat(exampleFeature)
	assert.NoError(t, err)

	// Modify the example feature to be simpler
	// The feature.ic file should work as is - no need to rename it
	featureIC := filepath.Join(exampleFeature, "feature.ic")
	
	simpleIntent := `# Example Feature

This is a simple test feature.

## Dependencies

Depends On: 

## Intent

Create a simple hello.txt file with "Hello, World!" content.

## Implementation Notes

Just create the file, nothing complex.
`
	err = os.WriteFile(featureIC, []byte(simpleIntent), 0644)
	require.NoError(t, err)

	// Update validations to match
	validationICV := filepath.Join(exampleFeature, "validations.icv")
	simpleValidation := `# Example Feature Validations

## File Check

Type: FileCheck

### Parameters
- Path: hello.txt
- Exists: true

### Description
Ensures the hello.txt file was created.
`
	err = os.WriteFile(validationICV, []byte(simpleValidation), 0644)
	require.NoError(t, err)

	// Update config to use mock agent for testing
	configContent := `version: 1

agent:
  provider: mock
  timeout: 5m
  retries: 3
  rate_limit: 1s

build:
  parallel: false
  cache_enabled: false
`
	err = os.WriteFile(configPath, []byte(configContent), 0644)
	require.NoError(t, err)

	// Now run build command
	buildForce = false
	buildDryRun = false
	err = runBuild(nil, []string{"example_feature"})
	assert.NoError(t, err)

	// Verify build created state directory
	stateDir := filepath.Join(".intentc", "state")
	_, err = os.Stat(stateDir)
	assert.NoError(t, err)

	// Verify status file exists
	statusFile := filepath.Join(stateDir, "status.json")
	_, err = os.Stat(statusFile)
	assert.NoError(t, err)
}

func TestInitWithExistingIntentcFile(t *testing.T) {
	// Create a temporary directory for the test
	tmpDir, err := os.MkdirTemp("", "init-existing-test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	// Save original working directory
	originalWD, err := os.Getwd()
	require.NoError(t, err)
	defer os.Chdir(originalWD)

	// Change to temp directory
	err = os.Chdir(tmpDir)
	require.NoError(t, err)

	// Initialize git repository
	gitMgr := git.NewGitManager(tmpDir)
	ctx := context.Background()
	err = gitMgr.Initialize(ctx, tmpDir)
	require.NoError(t, err)

	// Create an old-style .intentc file (not directory)
	oldConfig := `version: "1.0"
default_agent: claude-code
agents:
    claude-code:
        type: claude-code
        command: claude-code
settings:
    auto_validate: "true"
`
	err = os.WriteFile(".intentc", []byte(oldConfig), 0644)
	require.NoError(t, err)

	// Verify it's a file
	info, err := os.Stat(".intentc")
	require.NoError(t, err)
	assert.False(t, info.IsDir(), ".intentc should be a file initially")

	// Run init command - it should handle this gracefully
	err = runInit(nil, []string{})
	assert.NoError(t, err)

	// Verify .intentc is now a directory
	info, err = os.Stat(".intentc")
	require.NoError(t, err)
	assert.True(t, info.IsDir(), ".intentc should be a directory after init")

	// Verify config.yaml exists
	configPath := filepath.Join(".intentc", "config.yaml")
	_, err = os.Stat(configPath)
	assert.NoError(t, err)
}