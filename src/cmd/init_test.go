package cmd

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/pboueri/intentc/src/config"
	"github.com/pboueri/intentc/src/git"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gopkg.in/yaml.v3"
)

func TestInitCommand(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "init-cmd-test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	originalWD, err := os.Getwd()
	require.NoError(t, err)
	defer os.Chdir(originalWD)

	err = os.Chdir(tmpDir)
	require.NoError(t, err)

	ctx := context.Background()
	gitMgr := git.NewGitManager(tmpDir)
	err = gitMgr.Initialize(ctx, tmpDir)
	require.NoError(t, err)

	err = runInit(nil, []string{})
	require.NoError(t, err)

	intentDir := filepath.Join(tmpDir, "intent")
	_, err = os.Stat(intentDir)
	assert.NoError(t, err)

	projectIC := filepath.Join(intentDir, "project.ic")
	_, err = os.Stat(projectIC)
	assert.NoError(t, err)

	content, err := os.ReadFile(projectIC)
	require.NoError(t, err)
	assert.Contains(t, string(content), "# Project Intent")

	exampleDir := filepath.Join(intentDir, "example_feature")
	_, err = os.Stat(exampleDir)
	assert.NoError(t, err)

	featureIC := filepath.Join(exampleDir, "feature.ic")
	_, err = os.Stat(featureIC)
	assert.NoError(t, err)

	validationICV := filepath.Join(exampleDir, "validations.icv")
	_, err = os.Stat(validationICV)
	assert.NoError(t, err)

	// Check .intentc directory exists
	configDir := filepath.Join(tmpDir, ".intentc")
	_, err = os.Stat(configDir)
	assert.NoError(t, err)

	// Check config.yaml file exists
	configFile := filepath.Join(configDir, "config.yaml")
	configData, err := os.ReadFile(configFile)
	require.NoError(t, err)

	var config config.Config
	err = yaml.Unmarshal(configData, &config)
	require.NoError(t, err)

	assert.Equal(t, 1, config.Version)
	assert.Equal(t, "claude", config.Agent.Provider)
	assert.Equal(t, 5*time.Minute, config.Agent.Timeout)
}

func TestInitCommand_RequiresGit(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "init-no-git-test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	originalWD, err := os.Getwd()
	require.NoError(t, err)
	defer os.Chdir(originalWD)

	err = os.Chdir(tmpDir)
	require.NoError(t, err)

	err = runInit(nil, []string{})
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "requires a git repository")
}
