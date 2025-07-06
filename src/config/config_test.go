package config

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestLoadConfig_Default(t *testing.T) {
	// Create temporary directory without config file
	tmpDir := t.TempDir()

	cfg, err := LoadConfig(tmpDir)
	require.NoError(t, err)
	require.NotNil(t, cfg)

	// Check defaults
	assert.Equal(t, 1, cfg.Version)
	assert.Equal(t, "claude", cfg.Agent.Provider)
	assert.Equal(t, 5*time.Minute, cfg.Agent.Timeout)
	assert.Equal(t, 3, cfg.Agent.Retries)
	assert.Equal(t, 1*time.Second, cfg.Agent.RateLimit)
	assert.False(t, cfg.Build.Parallel)
	assert.False(t, cfg.Build.CacheEnabled)
}

func TestLoadConfig_FromFile(t *testing.T) {
	// Create temporary directory with config file
	tmpDir := t.TempDir()
	configDir := filepath.Join(tmpDir, ".intentc")
	err := os.MkdirAll(configDir, 0755)
	require.NoError(t, err)

	configContent := `version: 1

agent:
  provider: claude
  timeout: 10m
  retries: 5
  rate_limit: 2s

build:
  parallel: true
  cache_enabled: true
`

	configPath := filepath.Join(configDir, "config.yaml")
	err = os.WriteFile(configPath, []byte(configContent), 0644)
	require.NoError(t, err)

	cfg, err := LoadConfig(tmpDir)
	require.NoError(t, err)
	require.NotNil(t, cfg)

	// Check loaded values
	assert.Equal(t, 1, cfg.Version)
	assert.Equal(t, "claude", cfg.Agent.Provider)
	assert.Equal(t, 10*time.Minute, cfg.Agent.Timeout)
	assert.Equal(t, 5, cfg.Agent.Retries)
	assert.Equal(t, 2*time.Second, cfg.Agent.RateLimit)
	assert.True(t, cfg.Build.Parallel)
	assert.True(t, cfg.Build.CacheEnabled)
}

func TestLoadConfig_EmptyFile(t *testing.T) {
	// Create temporary directory with empty config file
	tmpDir := t.TempDir()
	configDir := filepath.Join(tmpDir, ".intentc")
	err := os.MkdirAll(configDir, 0755)
	require.NoError(t, err)

	configPath := filepath.Join(configDir, "config.yaml")
	err = os.WriteFile(configPath, []byte(""), 0644)
	require.NoError(t, err)

	cfg, err := LoadConfig(tmpDir)
	require.NoError(t, err)
	require.NotNil(t, cfg)

	// Should have defaults
	assert.Equal(t, 1, cfg.Version)
	assert.Equal(t, "claude", cfg.Agent.Provider)
	assert.Equal(t, 5*time.Minute, cfg.Agent.Timeout)
}

func TestLoadConfig_InvalidYAML(t *testing.T) {
	// Create temporary directory with invalid config file
	tmpDir := t.TempDir()
	configDir := filepath.Join(tmpDir, ".intentc")
	err := os.MkdirAll(configDir, 0755)
	require.NoError(t, err)

	configContent := `invalid yaml content
  with bad indentation
    and no structure
`

	configPath := filepath.Join(configDir, "config.yaml")
	err = os.WriteFile(configPath, []byte(configContent), 0644)
	require.NoError(t, err)

	_, err = LoadConfig(tmpDir)
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "failed to parse config file")
}

func TestSaveConfig(t *testing.T) {
	tmpDir := t.TempDir()

	cfg := &Config{
		Version: 1,
		Agent: AgentConfig{
			Provider:  "claude",
			Timeout:   10 * time.Minute,
			Retries:   4,
			RateLimit: 3 * time.Second,
		},
		Build: BuildConfig{
			Parallel:     true,
			CacheEnabled: false,
		},
	}

	err := SaveConfig(tmpDir, cfg)
	require.NoError(t, err)

	// Verify file exists
	configPath := filepath.Join(tmpDir, ".intentc", "config.yaml")
	_, err = os.Stat(configPath)
	require.NoError(t, err)

	// Load it back and verify
	loadedCfg, err := LoadConfig(tmpDir)
	require.NoError(t, err)

	assert.Equal(t, cfg.Version, loadedCfg.Version)
	assert.Equal(t, cfg.Agent.Provider, loadedCfg.Agent.Provider)
	assert.Equal(t, cfg.Agent.Timeout, loadedCfg.Agent.Timeout)
	assert.Equal(t, cfg.Agent.Retries, loadedCfg.Agent.Retries)
	assert.Equal(t, cfg.Agent.RateLimit, loadedCfg.Agent.RateLimit)
	assert.Equal(t, cfg.Build.Parallel, loadedCfg.Build.Parallel)
	assert.Equal(t, cfg.Build.CacheEnabled, loadedCfg.Build.CacheEnabled)
}

func TestSaveConfig_CreateDirectory(t *testing.T) {
	tmpDir := t.TempDir()
	// Don't create .intentc directory

	cfg := &Config{
		Version: 1,
		Agent: AgentConfig{
			Provider: "mock",
		},
	}

	err := SaveConfig(tmpDir, cfg)
	require.NoError(t, err)

	// Verify directory was created
	configDir := filepath.Join(tmpDir, ".intentc")
	info, err := os.Stat(configDir)
	require.NoError(t, err)
	assert.True(t, info.IsDir())
}