package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src/util"
	"github.com/pboueri/intentc/src/validation"
)

var listCmd = &cobra.Command{
	Use:   "list",
	Short: "List intents or validation types",
	Long:  `List all intent files in the project or available validation types.`,
}

var listIntentCmd = &cobra.Command{
	Use:   "intent",
	Short: "List all intent files",
	Long:  `Display a list of all intent files in the project.`,
	RunE:  runListIntent,
}

var listValidationCmd = &cobra.Command{
	Use:   "validation",
	Short: "List all available validation types",
	Long:  `Display a list of all available validation types with descriptions and examples.`,
	RunE:  runListValidation,
}

func init() {
	listCmd.AddCommand(listIntentCmd)
	listCmd.AddCommand(listValidationCmd)
}

func runListIntent(cmd *cobra.Command, args []string) error {
	projectRoot, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("failed to get current directory: %w", err)
	}

	// Check if .intentc exists
	configPath := filepath.Join(projectRoot, ".intentc")
	if !util.FileExists(configPath) {
		return fmt.Errorf("not in an intentc project (no .intentc found). Run 'intentc init' first")
	}

	// List all .ic files in the intent directory
	intentDir := filepath.Join(projectRoot, "intent")
	if !util.FileExists(intentDir) {
		fmt.Fprintln(cmd.OutOrStdout(), "No intent files found.")
		return nil
	}

	var intents []string
	err = filepath.Walk(intentDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if !info.IsDir() && strings.HasSuffix(info.Name(), ".ic") {
			relPath, _ := filepath.Rel(projectRoot, path)
			intents = append(intents, relPath)
		}
		return nil
	})

	if err != nil {
		return fmt.Errorf("failed to list intent files: %w", err)
	}

	if len(intents) == 0 {
		fmt.Fprintln(cmd.OutOrStdout(), "No intent files found.")
		return nil
	}

	fmt.Fprintln(cmd.OutOrStdout(), "Intent files:")
	for _, intent := range intents {
		fmt.Fprintf(cmd.OutOrStdout(), "  %s\n", intent)
	}

	return nil
}

func runListValidation(cmd *cobra.Command, args []string) error {
	// Use cmd.OutOrStdout() to respect output redirection in tests
	out := cmd.OutOrStdout()
	
	// Get validation types from centralized registry
	validationTypes := validation.GetValidationTypes()

	fmt.Fprintln(out, "Available Validation Types:")
	fmt.Fprintln(out, "==========================")
	fmt.Fprintln(out)

	for _, vt := range validationTypes {
		fmt.Fprintf(out, "Type: %s\n", vt.Type)
		fmt.Fprintf(out, "Description: %s\n", vt.Description)
		if vt.Category != "" {
			fmt.Fprintf(out, "Category: %s\n", vt.Category)
		}
		fmt.Fprintln(out, "\nExample:")
		fmt.Fprintln(out, "```")
		fmt.Fprintln(out, vt.Example)
		fmt.Fprintln(out, "```")
		fmt.Fprintln(out)
	}

	return nil
}