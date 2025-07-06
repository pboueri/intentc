package cmd

import (
	"fmt"
	"github.com/spf13/cobra"
)

var commitCmd = &cobra.Command{
	Use:   "commit",
	Short: "Commit changes",
	Long:  `Commit both intent and generated code changes to git with appropriate prefixes.`,
	RunE:  runCommit,
}

func runCommit(cmd *cobra.Command, args []string) error {
	fmt.Println("Commit command not yet implemented")
	return nil
}
