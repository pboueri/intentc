package cmd

import (
	"fmt"
	"github.com/spf13/cobra"
)

var buildCmd = &cobra.Command{
	Use:   "build [target]",
	Short: "Build targets from intents",
	Long:  `Build targets from intent files using AI agents. If no target is specified, builds all unbuilt targets.`,
	Args:  cobra.MaximumNArgs(1),
	RunE:  runBuild,
}

func runBuild(cmd *cobra.Command, args []string) error {
	fmt.Println("Build command not yet implemented")
	return nil
}
