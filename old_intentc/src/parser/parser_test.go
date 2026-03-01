package parser

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestParser_ParseIntentDirectory(t *testing.T) {
	// Create temp directory structure
	tmpDir := t.TempDir()
	intentDir := filepath.Join(tmpDir, "intent")
	require.NoError(t, os.MkdirAll(intentDir, 0755))

	// Create feature directories with different .ic file names
	feature1Dir := filepath.Join(intentDir, "feature1")
	require.NoError(t, os.MkdirAll(feature1Dir, 0755))
	require.NoError(t, os.WriteFile(filepath.Join(feature1Dir, "feature.ic"), []byte("# Feature 1"), 0644))

	feature2Dir := filepath.Join(intentDir, "feature2")
	require.NoError(t, os.MkdirAll(feature2Dir, 0755))
	require.NoError(t, os.WriteFile(filepath.Join(feature2Dir, "my-feature.ic"), []byte("# Feature 2"), 0644))

	// Create directory without .ic file
	feature3Dir := filepath.Join(intentDir, "feature3")
	require.NoError(t, os.MkdirAll(feature3Dir, 0755))
	require.NoError(t, os.WriteFile(filepath.Join(feature3Dir, "README.md"), []byte("Not an intent"), 0644))

	// Create directory with multiple .ic files (should still be discovered)
	feature4Dir := filepath.Join(intentDir, "feature4")
	require.NoError(t, os.MkdirAll(feature4Dir, 0755))
	require.NoError(t, os.WriteFile(filepath.Join(feature4Dir, "main.ic"), []byte("# Feature 4"), 0644))
	require.NoError(t, os.WriteFile(filepath.Join(feature4Dir, "alt.ic"), []byte("# Alt"), 0644))

	p := New()
	features, err := p.ParseIntentDirectory(intentDir)
	require.NoError(t, err)

	// Should find feature1, feature2, and feature4 (not feature3)
	assert.Len(t, features, 3)
	assert.Contains(t, features, "feature1")
	assert.Contains(t, features, "feature2")
	assert.Contains(t, features, "feature4")
}

func TestParser_FindIntentFile(t *testing.T) {
	tests := []struct {
		name      string
		files     map[string]string
		wantFile  string
		wantError string
	}{
		{
			name: "single ic file",
			files: map[string]string{
				"feature.ic":   "# Feature",
				"README.md":    "Documentation",
				"validation.icv": "Validations",
			},
			wantFile: "feature.ic",
		},
		{
			name: "different named ic file",
			files: map[string]string{
				"my-custom-name.ic": "# Feature",
				"notes.txt":         "Notes",
			},
			wantFile: "my-custom-name.ic",
		},
		{
			name: "no ic file",
			files: map[string]string{
				"README.md": "Documentation",
				"notes.txt": "Notes",
			},
			wantError: "no .ic file found",
		},
		{
			name: "multiple ic files",
			files: map[string]string{
				"feature.ic": "# Feature",
				"backup.ic":  "# Backup",
			},
			wantError: "multiple .ic files found",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tmpDir := t.TempDir()
			
			// Create test files
			for filename, content := range tt.files {
				err := os.WriteFile(filepath.Join(tmpDir, filename), []byte(content), 0644)
				require.NoError(t, err)
			}

			p := New()
			foundFile, err := p.FindIntentFile(tmpDir)

			if tt.wantError != "" {
				require.Error(t, err)
				assert.Contains(t, err.Error(), tt.wantError)
			} else {
				require.NoError(t, err)
				assert.Equal(t, filepath.Join(tmpDir, tt.wantFile), foundFile)
			}
		})
	}
}

func TestParser_ParseValidationFiles(t *testing.T) {
	tmpDir := t.TempDir()

	// Create validation files
	validation1 := `# Validations

## File Check

Type: FileCheck

### Parameters
- Path: main.go
- Exists: true

### Description
Main file should exist
`

	validation2 := `# More Validations

## Build Check

Type: CommandLineCheck

### Parameters
- Command: go build
- ExpectedExitCode: 0

### Description
Project should build
`

	require.NoError(t, os.WriteFile(filepath.Join(tmpDir, "basic.icv"), []byte(validation1), 0644))
	require.NoError(t, os.WriteFile(filepath.Join(tmpDir, "build.icv"), []byte(validation2), 0644))
	require.NoError(t, os.WriteFile(filepath.Join(tmpDir, "README.md"), []byte("Not a validation"), 0644))

	p := New()
	validations, err := p.ParseValidationFiles(tmpDir)
	require.NoError(t, err)

	// Should find 2 validation files
	assert.Len(t, validations, 2)
	
	// Check that we parsed the validations correctly
	totalValidations := 0
	for _, vf := range validations {
		totalValidations += len(vf.Validations)
	}
	assert.Equal(t, 2, totalValidations)
}