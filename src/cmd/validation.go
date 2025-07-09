package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/parser"
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
	
	validationTypes := []struct {
		Type        src.ValidationType
		Description string
		Example     string
	}{
		{
			Type:        src.ValidationTypeFileCheck,
			Description: "Validates file existence and content",
			Example: `## Check main.go exists
Type: FileCheck

### Parameters
- file: src/main.go
- exists: true
- contains: package main

### Description
Ensures the main.go file exists and contains the package declaration`,
		},
		{
			Type:        src.ValidationTypeFolderCheck,
			Description: "Validates directory existence and structure",
			Example: `## Check src directory
Type: FolderCheck

### Parameters
- folder: src
- exists: true
- min_files: 1

### Description
Ensures the src directory exists and contains at least one file`,
		},
		{
			Type:        src.ValidationTypeCommandLineCheck,
			Description: "Executes commands and validates output",
			Example: `## Test suite passes
Type: CommandLineCheck

### Parameters
- command: go test ./...
- exit_code: 0
- output_contains: PASS

### Description
Runs the test suite and ensures all tests pass`,
		},
		{
			Type:        src.ValidationTypeWebCheck,
			Description: "Tests web endpoints and content (uses AI agent)",
			Example: `## Homepage responds
Type: WebCheck

### Parameters
- url: http://localhost:8080
- check: Verify the homepage loads and contains a welcome message

### Description
Uses AI to check if the web server is running and serving content`,
		},
		{
			Type:        src.ValidationTypeProjectCheck,
			Description: "Validates overall project structure (uses AI agent)",
			Example: `## Project structure valid
Type: ProjectCheck

### Parameters
- check: Verify the project follows Go module structure with go.mod and proper package organization

### Description
Uses AI to validate the overall project structure and setup`,
		},
	}

	fmt.Fprintln(out, "Available Validation Types:")
	fmt.Fprintln(out, "==========================")
	fmt.Fprintln(out)

	for _, vt := range validationTypes {
		fmt.Fprintf(out, "Type: %s\n", vt.Type)
		fmt.Fprintf(out, "Description: %s\n", vt.Description)
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

	// Validate the validation type
	validTypes := []src.ValidationType{
		src.ValidationTypeFileCheck,
		src.ValidationTypeFolderCheck,
		src.ValidationTypeCommandLineCheck,
		src.ValidationTypeWebCheck,
		src.ValidationTypeProjectCheck,
	}

	var isValidType bool
	for _, vt := range validTypes {
		if strings.EqualFold(string(vt), validationType) {
			validationType = string(vt)
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

	// Generate validation file content based on type
	content := generateValidationContent(targetName, src.ValidationType(validationType))

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

func generateValidationContent(targetName string, validationType src.ValidationType) string {
	switch validationType {
	case src.ValidationTypeFileCheck:
		return fmt.Sprintf(`## Check %s main file
Type: FileCheck

### Parameters
- file: src/main.go
- exists: true
- contains: package main

### Description
Ensures the main source file exists and contains the expected package declaration
`, targetName)

	case src.ValidationTypeFolderCheck:
		return fmt.Sprintf(`## Check %s directory structure
Type: FolderCheck

### Parameters
- folder: src
- exists: true
- min_files: 1

### Description
Ensures the source directory exists and contains at least one file
`, targetName)

	case src.ValidationTypeCommandLineCheck:
		return fmt.Sprintf(`## %s tests pass
Type: CommandLineCheck

### Parameters
- command: go test ./...
- exit_code: 0
- output_contains: PASS

### Description
Runs the test suite and ensures all tests pass
`, targetName)

	case src.ValidationTypeWebCheck:
		return fmt.Sprintf(`## %s web service running
Type: WebCheck

### Parameters
- url: http://localhost:8080
- check: Verify the web service is running and responds with a 200 status code

### Description
Uses AI to check if the web service is accessible and functioning
`, targetName)

	case src.ValidationTypeProjectCheck:
		return fmt.Sprintf(`## %s project structure valid
Type: ProjectCheck

### Parameters
- check: Verify the project follows proper structure with required dependencies and configuration files

### Description
Uses AI to validate the overall project setup and organization
`, targetName)

	default:
		return fmt.Sprintf(`## %s validation
Type: %s

### Parameters
# Add parameters here

### Description
Add description here
`, targetName, validationType)
	}
}