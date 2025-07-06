package cmd

import (
	"fmt"
	"github.com/spf13/cobra"
)

var configCmd = &cobra.Command{
	Use:   "config",
	Short: "Configure intentc",
	Long:  `Configure intentc settings such as coding agents and src.`,
	RunE:  runConfig,
}

func runConfig(cmd *cobra.Command, args []string) error {
	fmt.Println("Config command not yet implemented")
	return nil
}
