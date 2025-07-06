package cmd

import (
	"fmt"
	"github.com/spf13/cobra"
)

var statusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show target status",
	Long:  `Show the current status of all targets, including what is out of date and when things were generated.`,
	RunE:  runStatus,
}

func runStatus(cmd *cobra.Command, args []string) error {
	fmt.Println("Status command not yet implemented")
	return nil
}
