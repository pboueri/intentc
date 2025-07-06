package cmd

import (
	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src/logger"
)

var (
	verboseCount int
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
}

func Execute() error {
	return rootCmd.Execute()
}

func init() {
	// Add persistent verbose flag that can be used multiple times
	rootCmd.PersistentFlags().CountVarP(&verboseCount, "verbose", "v", "Increase verbosity (use -vv for debug level)")
	
	rootCmd.AddCommand(initCmd)
	rootCmd.AddCommand(buildCmd)
	rootCmd.AddCommand(cleanCmd)
	rootCmd.AddCommand(checkCmd)
	rootCmd.AddCommand(statusCmd)
	rootCmd.AddCommand(validateCmd)
	rootCmd.AddCommand(refineCmd)
	rootCmd.AddCommand(commitCmd)
	rootCmd.AddCommand(checkoutCmd)
	rootCmd.AddCommand(configCmd)
	rootCmd.AddCommand(helpCmd)
}
