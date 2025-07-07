package config

import (
	"fmt"
	"os"
	"path/filepath"
	"time"

	"gopkg.in/yaml.v3"
	"github.com/pboueri/intentc/src/logger"
)

type Config struct {
	Version int           `yaml:"version"`
	Agent   AgentConfig   `yaml:"agent"`
	Build   BuildConfig   `yaml:"build"`
	Logging LoggingConfig `yaml:"logging"`
}

type AgentConfig struct {
	Provider  string               `yaml:"provider"`
	Command   string               `yaml:"command,omitempty"`   // For custom CLI agents
	Timeout   time.Duration        `yaml:"timeout"`
	Retries   int                  `yaml:"retries,omitempty"`
	RateLimit time.Duration        `yaml:"rate_limit,omitempty"`
	Config    map[string]interface{} `yaml:"config,omitempty"`
	CLIArgs   []string             `yaml:"cli_args,omitempty"`
}

type BuildConfig struct {
	Parallel     bool `yaml:"parallel"`
	CacheEnabled bool `yaml:"cache_enabled"`
}

type LoggingConfig struct {
	Level string      `yaml:"level"`
	Sinks []LogSink   `yaml:"sinks"`
}

type LogSink struct {
	Type     string `yaml:"type"`     // "console" or "file"
	Filename string `yaml:"filename,omitempty"` // For file sink
	UseStderr bool  `yaml:"use_stderr,omitempty"` // For console sink
	Colorize  bool  `yaml:"colorize,omitempty"`   // For console sink
}

func LoadConfig(projectRoot string) (*Config, error) {
	configPath := filepath.Join(projectRoot, ".intentc", "config.yaml")
	
	// Default config
	config := &Config{
		Version: 1,
		Agent: AgentConfig{
			Provider: "claude",
			Timeout:  5 * time.Minute,
			Retries:  3,
			RateLimit: 1 * time.Second,
			CLIArgs:  []string{},
		},
		Build: BuildConfig{
			Parallel:     false, // Sequential by default for git state tracking
			CacheEnabled: false,
		},
		Logging: LoggingConfig{
			Level: "info",
			Sinks: []LogSink{
				{
					Type:     "console",
					Colorize: true,
				},
			},
		},
	}

	// Check if config file exists
	data, err := os.ReadFile(configPath)
	if err != nil {
		if !os.IsNotExist(err) {
			return nil, fmt.Errorf("failed to read config file: %w", err)
		}
		// File doesn't exist, continue with defaults
	} else {
		// Parse YAML
		if err := yaml.Unmarshal(data, config); err != nil {
			return nil, fmt.Errorf("failed to parse config file: %w", err)
		}
	}

	return config, nil
}

func SaveConfig(projectRoot string, config *Config) error {
	configPath := filepath.Join(projectRoot, ".intentc", "config.yaml")
	
	// Ensure directory exists
	if err := os.MkdirAll(filepath.Dir(configPath), 0755); err != nil {
		return fmt.Errorf("failed to create config directory: %w", err)
	}

	// Marshal to YAML
	data, err := yaml.Marshal(config)
	if err != nil {
		return fmt.Errorf("failed to marshal config: %w", err)
	}

	// Write file
	if err := os.WriteFile(configPath, data, 0644); err != nil {
		return fmt.Errorf("failed to write config file: %w", err)
	}

	return nil
}

// InitializeLogger sets up the logger based on config
func InitializeLogger(config *Config, projectRoot string) error {
	// Parse log level
	level, err := logger.ParseLevel(config.Logging.Level)
	if err != nil {
		return fmt.Errorf("invalid log level: %w", err)
	}

	// Create sinks
	var sinks []logger.Sink
	for _, sinkConfig := range config.Logging.Sinks {
		switch sinkConfig.Type {
		case "console":
			sink := logger.NewConsoleSink(sinkConfig.UseStderr, sinkConfig.Colorize)
			sinks = append(sinks, sink)
		case "file":
			filename := sinkConfig.Filename
			if filename == "" {
				filename = "intentc.log"
			}
			// If not absolute path, make it relative to project root
			if !filepath.IsAbs(filename) {
				filename = filepath.Join(projectRoot, ".intentc", filename)
			}
			sink, err := logger.NewFileSink(filename)
			if err != nil {
				return fmt.Errorf("failed to create file sink: %w", err)
			}
			sinks = append(sinks, sink)
		default:
			return fmt.Errorf("unknown sink type: %s", sinkConfig.Type)
		}
	}

	// Initialize logger
	logger.Initialize(sinks...)
	logger.SetLevel(level)

	return nil
}