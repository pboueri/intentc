package validation

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/pboueri/intentc/src"
)

type FileCheckValidator struct{}

func NewFileCheckValidator() *FileCheckValidator {
	return &FileCheckValidator{}
}

func (v *FileCheckValidator) GetType() src.ValidationType {
	return src.ValidationTypeFileCheck
}

func (v *FileCheckValidator) Validate(ctx context.Context, validation *src.Validation, projectPath string) (*ValidationResult, error) {
	result := &ValidationResult{
		ValidatedAt: time.Now(),
		Details:     []string{},
	}

	// Extract parameters
	fileParam, ok := validation.Parameters["file"]
	if !ok {
		return nil, fmt.Errorf("missing required parameter 'file'")
	}

	fileName, ok := fileParam.(string)
	if !ok {
		return nil, fmt.Errorf("parameter 'file' must be a string")
	}

	filePath := filepath.Join(projectPath, fileName)

	// Check existence
	existsParam, hasExists := validation.Parameters["exists"]
	if hasExists {
		shouldExist, ok := existsParam.(bool)
		if !ok {
			return nil, fmt.Errorf("parameter 'exists' must be a boolean")
		}

		_, err := os.Stat(filePath)
		exists := err == nil

		if exists == shouldExist {
			result.Passed = true
			if shouldExist {
				result.Message = fmt.Sprintf("File %s exists as expected", fileName)
			} else {
				result.Message = fmt.Sprintf("File %s does not exist as expected", fileName)
			}
		} else {
			result.Passed = false
			if shouldExist {
				result.Message = fmt.Sprintf("File %s was expected to exist but was not found", fileName)
			} else {
				result.Message = fmt.Sprintf("File %s was not expected to exist but was found", fileName)
			}
		}
		result.Details = append(result.Details, fmt.Sprintf("Path checked: %s", filePath))
	}

	// Check content contains
	containsParam, hasContains := validation.Parameters["contains"]
	if hasContains && (result.Passed || !hasExists) {
		containsStr, ok := containsParam.(string)
		if !ok {
			return nil, fmt.Errorf("parameter 'contains' must be a string")
		}

		content, err := os.ReadFile(filePath)
		if err != nil {
			result.Passed = false
			result.Message = fmt.Sprintf("Failed to read file %s: %v", fileName, err)
			return result, nil
		}

		fileContent := string(content)
		if contains(fileContent, containsStr) {
			result.Passed = true
			result.Message = fmt.Sprintf("File %s contains expected text: %s", fileName, containsStr)
			result.Details = append(result.Details, fmt.Sprintf("File contains expected text: %s", containsStr))
		} else {
			result.Passed = false
			result.Message = fmt.Sprintf("File %s does not contain expected text: %s (actual content: %q)", fileName, containsStr, fileContent)
		}
	}

	// If no specific checks were requested, just check that the file exists
	if !hasExists && !hasContains {
		_, err := os.Stat(filePath)
		if err == nil {
			result.Passed = true
			result.Message = fmt.Sprintf("File %s exists", fileName)
		} else {
			result.Passed = false
			result.Message = fmt.Sprintf("File %s not found", fileName)
		}
	}

	return result, nil
}

func contains(s, substr string) bool {
	return len(substr) > 0 && len(s) >= len(substr) && (s == substr || len(s) > len(substr) && (s[:len(substr)] == substr || contains(s[1:], substr)))
}