package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/logger"
	"github.com/pboueri/intentc/src/parser"
	"github.com/pboueri/intentc/src/util"
	"github.com/pboueri/intentc/src/validation"
)

var addCmd = &cobra.Command{
	Use:   "add",
	Short: "Add new intents or validations",
	Long:  `Add new intent files or validation templates to your project.`,
}

var addIntentCmd = &cobra.Command{
	Use:   "intent [name]",
	Short: "Add a new intent file",
	Long:  `Create a new intent file with a bare bones template.`,
	Args:  cobra.ExactArgs(1),
	RunE:  runAddIntent,
}

var addValidationCmd = &cobra.Command{
	Use:   "validation [target] [validation-type]",
	Short: "Add a validation stub to a target",
	Long:  `Add a validation template file (.icv) for a specific validation type to a target.`,
	Args:  cobra.ExactArgs(2),
	RunE:  runAddValidation,
}

func init() {
	addCmd.AddCommand(addIntentCmd)
	addCmd.AddCommand(addValidationCmd)
}

func runAddIntent(cmd *cobra.Command, args []string) error {
	name := args[0]
	
	// Validate name (no special characters except underscore and hyphen)
	if !isValidName(name) {
		return fmt.Errorf("invalid intent name: %s. Use only letters, numbers, underscores, and hyphens", name)
	}

	projectRoot, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("failed to get current directory: %w", err)
	}

	// Check if .intentc exists
	configPath := filepath.Join(projectRoot, ".intentc")
	if !util.FileExists(configPath) {
		return fmt.Errorf("not in an intentc project (no .intentc found). Run 'intentc init' first")
	}

	// Create intent directory path
	intentDir := filepath.Join(projectRoot, "intent", name)
	if err := os.MkdirAll(intentDir, 0755); err != nil {
		return fmt.Errorf("failed to create intent directory: %w", err)
	}

	// Create intent file path
	intentPath := filepath.Join(intentDir, name+".ic")
	
	// Check if file already exists
	if util.FileExists(intentPath) {
		return fmt.Errorf("intent file %s already exists", intentPath)
	}

	// Create intent content using the same template as init
	intentContent := fmt.Sprintf(`# %s

This intent defines the %s feature.

## Dependencies

Depends On: 

## Intent

[Describe what this feature should do]

## Implementation Notes

[Any specific implementation guidance]
`, toTitle(name), name)

	// Write intent file
	if err := os.WriteFile(intentPath, []byte(intentContent), 0644); err != nil {
		return fmt.Errorf("failed to write intent file: %w", err)
	}

	logger.Info("✓ Created intent file: %s", intentPath)
	logger.Info("\nNext steps:")
	logger.Info("1. Edit %s to define your intent", intentPath)
	logger.Info("2. Add dependencies if this intent depends on other features")
	logger.Info("3. Run 'intentc add validation %s <validation-type>' to add validations", name)
	logger.Info("4. Run 'intentc build %s' to generate code from this intent", name)

	return nil
}

func runAddValidation(cmd *cobra.Command, args []string) error {
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
		return fmt.Errorf("invalid validation type: %s. Run 'intentc list validation' to see available types", validationType)
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

func isValidName(name string) bool {
	if name == "" {
		return false
	}
	for _, ch := range name {
		if !((ch >= 'a' && ch <= 'z') || 
		     (ch >= 'A' && ch <= 'Z') || 
		     (ch >= '0' && ch <= '9') || 
		     ch == '-' || ch == '_') {
			return false
		}
	}
	return true
}

func toTitle(name string) string {
	// Convert snake_case or kebab-case to Title Case
	words := strings.FieldsFunc(name, func(r rune) bool {
		return r == '_' || r == '-'
	})
	
	for i, word := range words {
		if len(word) > 0 {
			words[i] = strings.ToUpper(word[:1]) + word[1:]
		}
	}
	
	return strings.Join(words, " ")
}