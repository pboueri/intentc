package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/parser"
	"github.com/pboueri/intentc/src/validation"
)

var validationCmd = &cobra.Command{
	Use:   "validation",
	Short: "Manage validation types and templates",
	Long:  `Commands for listing validation types and adding validation templates to targets.`,
}

var validationListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all available validation types",
	Long:  `Display a list of all available validation types with descriptions and examples.`,
	RunE:  runValidationList,
}

var validationAddCmd = &cobra.Command{
	Use:   "add [target] [validation-type]",
	Short: "Add a validation stub to a target",
	Long:  `Add a validation template file (.icv) for a specific validation type to a target.`,
	Args:  cobra.ExactArgs(2),
	RunE:  runValidationAdd,
}

func init() {
	validationCmd.AddCommand(validationListCmd)
	validationCmd.AddCommand(validationAddCmd)
}

func runValidationList(cmd *cobra.Command, args []string) error {
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

func runValidationAdd(cmd *cobra.Command, args []string) error {
	targetName := args[0]
	validationType := args[1]

	// Validate the validation type using centralized registry
	var isValidType bool
	for _, vt := range validation.GetValidationTypes() {
		if strings.EqualFold(string(vt.Type), validationType) {
			validationType = string(vt.Type)
			isValidType = true
			break
		}
	}

	if !isValidType {
		return fmt.Errorf("invalid validation type: %s. Run 'intentc validation list' to see available types", validationType)
	}

	projectRoot, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("failed to get current directory: %w", err)
	}

	// Check if .intentc exists
	configPath := filepath.Join(projectRoot, ".intentc")
	if _, err := os.Stat(configPath); os.IsNotExist(err) {
		return fmt.Errorf("not in an intentc project (no .intentc found). Run 'intentc init' first")
	}

	// Create target registry and load targets
	targetRegistry := parser.NewTargetRegistry(projectRoot)
	if err := targetRegistry.LoadTargets(); err != nil {
		return fmt.Errorf("failed to load targets: %w", err)
	}

	// Get target from registry
	targetInfo, exists := targetRegistry.GetTarget(targetName)
	if !exists {
		return fmt.Errorf("target %s not found", targetName)
	}

	if targetInfo.Intent == nil {
		return fmt.Errorf("target %s has no intent file", targetName)
	}

	// Generate validation file content based on type using centralized registry
	content := validation.GenerateValidationTemplate(targetName, src.ValidationType(validationType))

	// Create validation file path
	targetDir := filepath.Dir(targetInfo.IntentPath)
	validationFileName := fmt.Sprintf("%s-%s.icv", targetName, strings.ToLower(validationType))
	validationPath := filepath.Join(targetDir, validationFileName)

	// Check if file already exists
	if _, err := os.Stat(validationPath); err == nil {
		return fmt.Errorf("validation file %s already exists", validationPath)
	}

	// Write validation file
	if err := os.WriteFile(validationPath, []byte(content), 0644); err != nil {
		return fmt.Errorf("failed to write validation file: %w", err)
	}

	fmt.Fprintf(cmd.OutOrStdout(), "Created validation file: %s\n", validationPath)
	fmt.Fprintln(cmd.OutOrStdout(), "Edit this file to customize the validation parameters.")

	return nil
}

