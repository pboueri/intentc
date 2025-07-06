package cmd

import (
	"github.com/spf13/cobra"
)

var rootCmd = &cobra.Command{
	Use:   "intentc",
	Short: "Compiler of Intent - Transform intents into code using AI agents",
	Long: `intentc is a tool that transforms loosely specified intents into precise code 
using AI coding agents, inspired by GNU Make's declarative approach to build management.`,
}

func Execute() error {
	return rootCmd.Execute()
}

func init() {
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
