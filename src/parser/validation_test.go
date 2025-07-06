package parser

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/pboueri/intentc/src"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestValidationParser_ParseValidationFile(t *testing.T) {
	content := `# Test Validations

## File Structure Check

Type: FileCheck

### Parameters
- Path: src/main.go
- Exists: true
- Contains: package main

### Description
Ensures the main file exists with correct package.

## Build Check

Type: CommandLineCheck
Hidden: true

### Parameters
- Command: go build ./...
- ExpectedExitCode: 0

### Description
Ensures the project builds successfully.`

	tmpDir, err := os.MkdirTemp("", "validation-parser-test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	validationFile := filepath.Join(tmpDir, "validations.icv")
	err = os.WriteFile(validationFile, []byte(content), 0644)
	require.NoError(t, err)

	parser := NewValidationParser()
	result, err := parser.ParseValidationFile(validationFile)
	require.NoError(t, err)

	assert.Equal(t, validationFile, result.FilePath)
	assert.Len(t, result.Validations, 2)

	fileCheck := result.Validations[0]
	assert.Equal(t, "File Structure Check", fileCheck.Name)
	assert.Equal(t, src.ValidationType("FileCheck"), fileCheck.Type)
	assert.Equal(t, "Ensures the main file exists with correct package.", fileCheck.Description)
	assert.False(t, fileCheck.Hidden)
	assert.Equal(t, "src/main.go", fileCheck.Parameters["Path"])
	assert.Equal(t, true, fileCheck.Parameters["Exists"])
	assert.Equal(t, "package main", fileCheck.Parameters["Contains"])

	buildCheck := result.Validations[1]
	assert.Equal(t, "Build Check", buildCheck.Name)
	assert.Equal(t, src.ValidationType("CommandLineCheck"), buildCheck.Type)
	assert.Equal(t, "Ensures the project builds successfully.", buildCheck.Description)
	assert.True(t, buildCheck.Hidden)
	assert.Equal(t, "go build ./...", buildCheck.Parameters["Command"])
	assert.Equal(t, "0", buildCheck.Parameters["ExpectedExitCode"])
}

func TestValidationParser_FindValidationFiles(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "validation-find-test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	intentDir := filepath.Join(tmpDir, "intent")
	err = os.MkdirAll(intentDir, 0755)
	require.NoError(t, err)

	feature1Dir := filepath.Join(intentDir, "feature1")
	err = os.MkdirAll(feature1Dir, 0755)
	require.NoError(t, err)

	validation1 := filepath.Join(feature1Dir, "validations.icv")
	err = os.WriteFile(validation1, []byte("# Validations"), 0644)
	require.NoError(t, err)

	feature2Dir := filepath.Join(intentDir, "feature2")
	err = os.MkdirAll(feature2Dir, 0755)
	require.NoError(t, err)

	validation2 := filepath.Join(feature2Dir, "checks.icv")
	err = os.WriteFile(validation2, []byte("# Checks"), 0644)
	require.NoError(t, err)

	notValidation := filepath.Join(intentDir, "feature.ic")
	err = os.WriteFile(notValidation, []byte("Not a validation file"), 0644)
	require.NoError(t, err)

	parser := NewValidationParser()
	files, err := parser.FindValidationFiles(intentDir)
	require.NoError(t, err)

	assert.Len(t, files, 2)
	assert.Contains(t, files, validation1)
	assert.Contains(t, files, validation2)
}
