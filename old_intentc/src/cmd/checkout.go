package cmd

import (
	"fmt"
	"github.com/spf13/cobra"
)

var checkoutCmd = &cobra.Command{
	Use:   "checkout [target] [generation-id]",
	Short: "Checkout a previous generation",
	Long:  `Rollback to a prior state by checking out a specific generation ID for a target.`,
	Args:  cobra.ExactArgs(2),
	RunE:  runCheckout,
}

func runCheckout(cmd *cobra.Command, args []string) error {
	fmt.Println("Checkout command not yet implemented")
	return nil
}
