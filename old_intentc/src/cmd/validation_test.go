package cmd

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"

	"github.com/spf13/cobra"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestValidationListCommand(t *testing.T) {
	// Create a buffer to capture output
	buf := new(bytes.Buffer)
	
	// Create the command
	cmd := listValidationCmd
	cmd.SetOut(buf)
	cmd.SetErr(buf)
	
	// Execute the command directly by calling its RunE function
	err := runListValidation(cmd, []string{})
	require.NoError(t, err)
	
	// Check the output contains all validation types
	output := buf.String()
	assert.Contains(t, output, "Available Validation Types:")
	assert.Contains(t, output, "Type: FileCheck")
	assert.Contains(t, output, "Type: FolderCheck")
	assert.Contains(t, output, "Type: CommandLineCheck")
	assert.Contains(t, output, "Type: WebCheck")
	assert.Contains(t, output, "Type: ProjectCheck")
	
	// Check descriptions are present
	assert.Contains(t, output, "Validates file existence and content")
	assert.Contains(t, output, "Validates directory existence and structure")
	assert.Contains(t, output, "Executes commands and validates output")
	assert.Contains(t, output, "Tests web endpoints and content")
	assert.Contains(t, output, "Validates overall project structure")
	
	// Check examples are present
	assert.Contains(t, output, "Example:")
	assert.Contains(t, output, "### Parameters")
	assert.Contains(t, output, "### Description")
}

func TestValidationAddCommand(t *testing.T) {
	// Create a temporary directory for testing
	tempDir := t.TempDir()
	
	// Change to temp directory
	oldDir, err := os.Getwd()
	require.NoError(t, err)
	err = os.Chdir(tempDir)
	require.NoError(t, err)
	defer os.Chdir(oldDir)
	
	// Create .intentc directory to simulate initialized project
	err = os.MkdirAll(".intentc", 0755)
	require.NoError(t, err)
	
	// Create the intent directory structure
	intentDir := filepath.Join("intent", "test-target")
	err = os.MkdirAll(intentDir, 0755)
	require.NoError(t, err)
	
	// Create a minimal intent file
	intentContent := `## Intent: test-target
Purpose: Test target for validation commands

### Target
name: test-target
`
	intentPath := filepath.Join(intentDir, "intent.ic")
	err = os.WriteFile(intentPath, []byte(intentContent), 0644)
	require.NoError(t, err)
	
	tests := []struct {
		name           string
		args           []string
		expectError    bool
		errorContains  string
		checkFile      string
		checkContent   string
	}{
		{
			name:          "missing arguments",
			args:          []string{},
			expectError:   true,
			errorContains: "accepts 2 arg(s)",
		},
		{
			name:          "invalid target",
			args:          []string{"nonexistent", "FileCheck"},
			expectError:   true,
			errorContains: "target nonexistent not found",
		},
		{
			name:          "invalid validation type",
			args:          []string{"test-target", "InvalidType"},
			expectError:   true,
			errorContains: "invalid validation type",
		},
		{
			name:        "valid FileCheck",
			args:        []string{"test-target", "FileCheck"},
			expectError: false,
			checkFile:   filepath.Join(intentDir, "test-target-filecheck.icv"),
			checkContent: "Type: FileCheck",
		},
		{
			name:        "valid FolderCheck",
			args:        []string{"test-target", "FolderCheck"},
			expectError: false,
			checkFile:   filepath.Join(intentDir, "test-target-foldercheck.icv"),
			checkContent: "Type: FolderCheck",
		},
		{
			name:        "valid CommandLineCheck",
			args:        []string{"test-target", "CommandLineCheck"},
			expectError: false,
			checkFile:   filepath.Join(intentDir, "test-target-commandlinecheck.icv"),
			checkContent: "Type: CommandLineCheck",
		},
	}
	
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Create a buffer to capture output
			buf := new(bytes.Buffer)
			
			// Create a new command instance for each test
			cmd := &cobra.Command{
				Use:   "add [target] [validation-type]",
				Short: "Add a validation stub to a target",
				Args:  cobra.ExactArgs(2),
				RunE:  runAddValidation,
			}
			cmd.SetOut(buf)
			cmd.SetErr(buf)
			cmd.SetArgs(tt.args)
			
			// Execute the command
			err := cmd.Execute()
			
			if tt.expectError {
				assert.Error(t, err)
				if tt.errorContains != "" {
					assert.Contains(t, err.Error(), tt.errorContains)
				}
			} else {
				assert.NoError(t, err)
				
				// Check the file was created
				if tt.checkFile != "" {
					assert.FileExists(t, tt.checkFile)
					
					// Check file content
					content, err := os.ReadFile(tt.checkFile)
					require.NoError(t, err)
					assert.Contains(t, string(content), tt.checkContent)
					
					// Clean up for next test
					os.Remove(tt.checkFile)
				}
				
				// Check output
				output := buf.String()
				assert.Contains(t, output, "Created validation file:")
				assert.Contains(t, output, "Edit this file to customize")
			}
		})
	}
}

func TestValidationAddCommandFileExists(t *testing.T) {
	// Create a temporary directory for testing
	tempDir := t.TempDir()
	
	// Change to temp directory
	oldDir, err := os.Getwd()
	require.NoError(t, err)
	err = os.Chdir(tempDir)
	require.NoError(t, err)
	defer os.Chdir(oldDir)
	
	// Create .intentc directory
	err = os.MkdirAll(".intentc", 0755)
	require.NoError(t, err)
	
	// Create the intent directory structure
	intentDir := filepath.Join("intent", "test-target")
	err = os.MkdirAll(intentDir, 0755)
	require.NoError(t, err)
	
	// Create intent file
	intentContent := `## Intent: test-target
Purpose: Test target
`
	intentPath := filepath.Join(intentDir, "intent.ic")
	err = os.WriteFile(intentPath, []byte(intentContent), 0644)
	require.NoError(t, err)
	
	// Create existing validation file
	validationPath := filepath.Join(intentDir, "test-target-filecheck.icv")
	err = os.WriteFile(validationPath, []byte("existing content"), 0644)
	require.NoError(t, err)
	
	// Try to add validation that already exists
	cmd := &cobra.Command{
		Use:   "add [target] [validation-type]",
		Short: "Add a validation stub to a target",
		Args:  cobra.ExactArgs(2),
		RunE:  runAddValidation,
	}
	cmd.SetArgs([]string{"test-target", "FileCheck"})
	
	err = cmd.Execute()
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "already exists")
}

// TestGenerateValidationContent removed - generateValidationContent function no longer exists
// The validation generation is now handled by the validation command itself

func TestValidationCommandStructure(t *testing.T) {
	// Test that list and add commands have validation subcommands
	assert.NotNil(t, listCmd)
	assert.NotNil(t, addCmd)
	
	// Check list command has validation subcommand
	var hasListValidation bool
	for _, cmd := range listCmd.Commands() {
		if cmd.Use == "validation" {
			hasListValidation = true
			break
		}
	}
	assert.True(t, hasListValidation, "list command should have 'validation' subcommand")
	
	// Check add command has validation subcommand
	var hasAddValidation bool
	for _, cmd := range addCmd.Commands() {
		if cmd.Use == "validation [target] [validation-type]" {
			hasAddValidation = true
			break
		}
	}
	assert.True(t, hasAddValidation, "add command should have 'validation' subcommand")
}

func TestValidationTypeCaseInsensitive(t *testing.T) {
	// Create a temporary directory for testing
	tempDir := t.TempDir()
	
	// Change to temp directory
	oldDir, err := os.Getwd()
	require.NoError(t, err)
	err = os.Chdir(tempDir)
	require.NoError(t, err)
	defer os.Chdir(oldDir)
	
	// Create .intentc directory
	err = os.MkdirAll(".intentc", 0755)
	require.NoError(t, err)
	
	// Create the intent directory structure
	intentDir := filepath.Join("intent", "test-target")
	err = os.MkdirAll(intentDir, 0755)
	require.NoError(t, err)
	
	// Create intent file
	intentPath := filepath.Join(intentDir, "intent.ic")
	err = os.WriteFile(intentPath, []byte("## Intent: test-target"), 0644)
	require.NoError(t, err)
	
	// Test case variations
	caseVariations := []string{
		"filecheck",
		"FILECHECK",
		"FileCheck",
		"fileCheck",
	}
	
	for i, variation := range caseVariations {
		t.Run(variation, func(t *testing.T) {
			cmd := &cobra.Command{
				Use:   "add [target] [validation-type]",
				Short: "Add a validation stub to a target",
				Args:  cobra.ExactArgs(2),
				RunE:  runAddValidation,
			}
			cmd.SetArgs([]string{"test-target", variation})
			
			err := cmd.Execute()
			assert.NoError(t, err)
			
			// Check that file was created with normalized name
			expectedFile := filepath.Join(intentDir, "test-target-filecheck.icv")
			assert.FileExists(t, expectedFile)
			
			// Clean up for next iteration
			if i < len(caseVariations)-1 {
				os.Remove(expectedFile)
			}
		})
	}
}