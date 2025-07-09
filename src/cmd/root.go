package cmd

import (
	"fmt"
	"time"
	
	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src/config"
	"github.com/pboueri/intentc/src/logger"
)

var (
	verboseCount int
	configFile   string
	
	// Config override flags
	agentProvider  string
	agentCommand   string
	agentTimeout   string
	agentRetries   int
	agentRateLimit string
	agentCLIArgs   []string
	buildParallel  bool
	buildCache     bool
	logLevel       string
)

var rootCmd = &cobra.Command{
	Use:   "intentc",
	Short: "Compiler of Intent - Transform intents into code using AI agents",
	Long: `intentc is a tool that transforms loosely specified intents into precise code 
using AI coding agents, inspired by GNU Make's declarative approach to build management.`,
	PersistentPreRun: func(cmd *cobra.Command, args []string) {
		// Set logger level based on verbose flag count
		switch verboseCount {
		case 0:
			logger.SetLevel(logger.WarnLevel)
		case 1:
			logger.SetLevel(logger.InfoLevel)
		default: // 2 or more
			logger.SetLevel(logger.DebugLevel)
		}
	},
	Run: func(cmd *cobra.Command, args []string) {
		cmd.Help()
	},
}

func Execute() error {
	return rootCmd.Execute()
}

func init() {
	// Add persistent verbose flag that can be used multiple times
	rootCmd.PersistentFlags().CountVarP(&verboseCount, "verbose", "v", "Increase verbosity (use -vv for debug level)")
	
	// Config override flags
	rootCmd.PersistentFlags().StringVar(&configFile, "config", "", "Config file to use for overrides")
	rootCmd.PersistentFlags().StringVar(&agentProvider, "agent-provider", "", "Override agent provider (e.g., claude, cli)")
	rootCmd.PersistentFlags().StringVar(&agentCommand, "agent-command", "", "Override agent command (for CLI agents)")
	rootCmd.PersistentFlags().StringVar(&agentTimeout, "agent-timeout", "", "Override agent timeout (e.g., 5m, 30s)")
	rootCmd.PersistentFlags().IntVar(&agentRetries, "agent-retries", -1, "Override agent retries")
	rootCmd.PersistentFlags().StringVar(&agentRateLimit, "agent-rate-limit", "", "Override agent rate limit (e.g., 1s, 500ms)")
	rootCmd.PersistentFlags().StringSliceVar(&agentCLIArgs, "agent-cli-args", nil, "Override agent CLI arguments")
	rootCmd.PersistentFlags().BoolVar(&buildParallel, "build-parallel", false, "Enable parallel builds")
	rootCmd.PersistentFlags().BoolVar(&buildCache, "build-cache", false, "Enable build cache")
	rootCmd.PersistentFlags().StringVar(&logLevel, "log-level", "", "Override log level (debug, info, warn, error)")
	
	rootCmd.AddCommand(initCmd)
	rootCmd.AddCommand(intentCmd)
	rootCmd.AddCommand(buildCmd)
	rootCmd.AddCommand(cleanCmd)
	rootCmd.AddCommand(checkCmd)
	rootCmd.AddCommand(statusCmd)
	rootCmd.AddCommand(validateCmd)
	rootCmd.AddCommand(validationCmd)
	rootCmd.AddCommand(refineCmd)
	rootCmd.AddCommand(commitCmd)
	rootCmd.AddCommand(checkoutCmd)
	rootCmd.AddCommand(configCmd)
	rootCmd.AddCommand(helpCmd)
	rootCmd.AddCommand(decompileCmd)
}

// LoadConfigWithOverrides loads config and applies command-line overrides
func LoadConfigWithOverrides(projectRoot string) (*config.Config, error) {
	// Load base configuration
	cfg, err := config.LoadConfig(projectRoot)
	if err != nil {
		return nil, fmt.Errorf("failed to load config: %w", err)
	}

	// Apply config overrides from command line
	overrides, err := GetConfigOverrides()
	if err != nil {
		return nil, fmt.Errorf("failed to parse config overrides: %w", err)
	}
	
	return config.MergeConfig(cfg, overrides), nil
}

// GetConfigOverrides creates a Config struct from command-line flags
func GetConfigOverrides() (*config.Config, error) {
	override := &config.Config{}
	hasOverrides := false

	// Check if config file is specified
	if configFile != "" {
		fileConfig, err := config.LoadConfigFromFile(configFile)
		if err != nil {
			return nil, err
		}
		override = fileConfig
		hasOverrides = true
	}

	// Apply CLI flag overrides
	if agentProvider != "" {
		override.Agent.Provider = agentProvider
		hasOverrides = true
	}
	if agentCommand != "" {
		override.Agent.Command = agentCommand
		hasOverrides = true
	}
	if agentTimeout != "" {
		duration, err := time.ParseDuration(agentTimeout)
		if err != nil {
			return nil, err
		}
		override.Agent.Timeout = duration
		hasOverrides = true
	}
	if agentRetries >= 0 {
		override.Agent.Retries = agentRetries
		hasOverrides = true
	}
	if agentRateLimit != "" {
		duration, err := time.ParseDuration(agentRateLimit)
		if err != nil {
			return nil, err
		}
		override.Agent.RateLimit = duration
		hasOverrides = true
	}
	if agentCLIArgs != nil {
		override.Agent.CLIArgs = agentCLIArgs
		hasOverrides = true
	}
	if buildParallel {
		override.Build.Parallel = buildParallel
		hasOverrides = true
	}
	if buildCache {
		override.Build.CacheEnabled = buildCache
		hasOverrides = true
	}
	if logLevel != "" {
		override.Logging.Level = logLevel
		hasOverrides = true
	}

	if !hasOverrides {
		return nil, nil
	}

	return override, nil
}
