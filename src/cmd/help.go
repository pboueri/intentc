package cmd

import (
	"github.com/spf13/cobra"
)

var helpCmd = &cobra.Command{
	Use:   "help",
	Short: "Display help information",
	Long:  `Display help information about intentc commands.`,
	Run: func(cmd *cobra.Command, args []string) {
		rootCmd.Help()
	},
}
