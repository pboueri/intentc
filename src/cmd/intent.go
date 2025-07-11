package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src/logger"
	"github.com/pboueri/intentc/src/util"
)

var intentCmd = &cobra.Command{
	Use:   "intent",
	Short: "Manage intent files",
	Long:  `Commands for creating and understanding intent files.`,
}

var intentAddCmd = &cobra.Command{
	Use:   "add [name]",
	Short: "Add a new intent file",
	Long:  `Create a new intent file with a bare bones template.`,
	Args:  cobra.ExactArgs(1),
	RunE:  runIntentAdd,
}

var intentHelpCmd = &cobra.Command{
	Use:   "help",
	Short: "Show intent file schema",
	Long:  `Display the schema and structure of intent (.ic) files.`,
	Run:   runIntentHelp,
}

func init() {
	intentCmd.AddCommand(intentAddCmd)
	intentCmd.AddCommand(intentHelpCmd)
}

func runIntentAdd(cmd *cobra.Command, args []string) error {
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
	logger.Info("3. Run 'intentc validation add %s <validation-type>' to add validations", name)
	logger.Info("4. Run 'intentc build %s' to generate code from this intent", name)

	return nil
}

func runIntentHelp(cmd *cobra.Command, args []string) {
	fmt.Fprint(cmd.OutOrStdout(), `Intent File Schema (.ic files)
=============================

Intent files use Markdown format with specific sections that intentc recognizes.

Required Structure:
------------------

# Feature Name

A brief description of the feature.

## Dependencies

Depends On: comma,separated,list,of,dependencies
(Leave empty if no dependencies)

## Intent

Detailed description of what this feature should accomplish.
This is the main content that will be sent to the AI agent.

## Implementation Notes

Optional section for specific implementation guidance,
constraints, or technical requirements.

Example:
--------

# User Authentication

This intent defines the user authentication system.

## Dependencies

Depends On: database, config

## Intent

Create a secure user authentication system that supports:
- User registration with email verification
- Login with email/password
- Password reset functionality
- JWT-based session management
- Rate limiting for auth endpoints

## Implementation Notes

- Use bcrypt for password hashing
- Store sessions in Redis
- Follow OWASP guidelines for security

Schema Notes:
-------------

1. The file must be in Markdown format
2. The first H1 (#) heading is treated as the feature name
3. "Dependencies" section must use "Depends On:" format
4. Dependencies are comma-separated feature names
5. The "Intent" section contains the main specification
6. All other sections are treated as additional context
7. File must have .ic extension
8. File should be placed in intent/<feature-name>/ directory
`)
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