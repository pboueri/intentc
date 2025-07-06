package cmd

import (
	"fmt"
	"github.com/spf13/cobra"
)

var cleanCmd = &cobra.Command{
	Use:   "clean [target]",
	Short: "Clean generated files",
	Long:  `Clean generated files from a target. If no target is specified, cleans all generated files.`,
	Args:  cobra.MaximumNArgs(1),
	RunE:  runClean,
}

func runClean(cmd *cobra.Command, args []string) error {
	fmt.Println("Clean command not yet implemented")
	return nil
}
