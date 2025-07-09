package config

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestMergeConfig(t *testing.T) {
	base := &Config{
		Version: 1,
		Agent: AgentConfig{
			Provider:  "claude",
			Timeout:   5 * time.Minute,
			Retries:   3,
			RateLimit: 1 * time.Second,
			CLIArgs:   []string{"--original"},
		},
		Build: BuildConfig{
			Parallel:     false,
			CacheEnabled: false,
		},
		Logging: LoggingConfig{
			Level: "info",
			Sinks: []LogSink{
				{Type: "console", Colorize: true},
			},
		},
	}

	override := &Config{
		Agent: AgentConfig{
			Provider: "cli",
			Command:  "custom-cli",
			Timeout:  10 * time.Second,
			CLIArgs:  []string{"--override"},
		},
		Build: BuildConfig{
			Parallel: true,
		},
		Logging: LoggingConfig{
			Level: "debug",
		},
	}

	merged := MergeConfig(base, override)

	// Check merged values
	assert.Equal(t, "cli", merged.Agent.Provider)
	assert.Equal(t, "custom-cli", merged.Agent.Command)
	assert.Equal(t, 10*time.Second, merged.Agent.Timeout)
	assert.Equal(t, []string{"--override"}, merged.Agent.CLIArgs)
	assert.Equal(t, 3, merged.Agent.Retries) // Should keep base value
	assert.Equal(t, 1*time.Second, merged.Agent.RateLimit) // Should keep base value

	assert.True(t, merged.Build.Parallel)
	assert.False(t, merged.Build.CacheEnabled) // Should keep base value

	assert.Equal(t, "debug", merged.Logging.Level)
	assert.Equal(t, base.Logging.Sinks, merged.Logging.Sinks) // Should keep base value
}

func TestMergeConfigNilCases(t *testing.T) {
	base := &Config{
		Agent: AgentConfig{
			Provider: "claude",
		},
	}

	// Test nil override
	merged := MergeConfig(base, nil)
	assert.Equal(t, base, merged)

	// Test nil base
	override := &Config{
		Agent: AgentConfig{
			Provider: "cli",
		},
	}
	merged = MergeConfig(nil, override)
	assert.Equal(t, override, merged)
}

func TestLoadConfigFromFile(t *testing.T) {
	// Create a temporary config file
	tmpDir := t.TempDir()
	configFile := filepath.Join(tmpDir, "test-config.yaml")

	configContent := `version: 1
agent:
  provider: cli
  command: test-command
  timeout: 30s
  retries: 5
build:
  parallel: true
  cache_enabled: true
logging:
  level: debug`

	err := os.WriteFile(configFile, []byte(configContent), 0644)
	require.NoError(t, err)

	// Load config
	cfg, err := LoadConfigFromFile(configFile)
	require.NoError(t, err)

	// Verify loaded config
	assert.Equal(t, 1, cfg.Version)
	assert.Equal(t, "cli", cfg.Agent.Provider)
	assert.Equal(t, "test-command", cfg.Agent.Command)
	assert.Equal(t, 30*time.Second, cfg.Agent.Timeout)
	assert.Equal(t, 5, cfg.Agent.Retries)
	assert.True(t, cfg.Build.Parallel)
	assert.True(t, cfg.Build.CacheEnabled)
	assert.Equal(t, "debug", cfg.Logging.Level)
}

func TestLoadConfigFromFileError(t *testing.T) {
	// Test non-existent file
	_, err := LoadConfigFromFile("/non/existent/file.yaml")
	assert.Error(t, err)

	// Test invalid YAML
	tmpDir := t.TempDir()
	configFile := filepath.Join(tmpDir, "invalid.yaml")
	err = os.WriteFile(configFile, []byte("invalid: yaml: content:"), 0644)
	require.NoError(t, err)

	_, err = LoadConfigFromFile(configFile)
	assert.Error(t, err)
}