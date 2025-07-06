package cmd

import (
	"fmt"
	"github.com/spf13/cobra"
)

var validateCmd = &cobra.Command{
	Use:   "validate [target]",
	Short: "Run validations",
	Long:  `Run validations for a target and generate a report of what passed or failed.`,
	Args:  cobra.MaximumNArgs(1),
	RunE:  runValidate,
}

func runValidate(cmd *cobra.Command, args []string) error {
	fmt.Println("Validate command not yet implemented")
	return nil
}
