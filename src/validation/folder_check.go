package validation

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/pboueri/intentc/src"
)

type FolderCheckValidator struct{}

func NewFolderCheckValidator() *FolderCheckValidator {
	return &FolderCheckValidator{}
}

func (v *FolderCheckValidator) GetType() src.ValidationType {
	return src.ValidationTypeFolderCheck
}

func (v *FolderCheckValidator) Validate(ctx context.Context, validation *src.Validation, projectPath string) (*ValidationResult, error) {
	result := &ValidationResult{
		ValidatedAt: time.Now(),
		Details:     []string{},
	}

	// Extract parameters
	folderParam, ok := validation.Parameters["folder"]
	if !ok {
		return nil, fmt.Errorf("missing required parameter 'folder'")
	}

	folderName, ok := folderParam.(string)
	if !ok {
		return nil, fmt.Errorf("parameter 'folder' must be a string")
	}

	folderPath := filepath.Join(projectPath, folderName)

	// Check existence
	existsParam, hasExists := validation.Parameters["exists"]
	if hasExists {
		shouldExist, ok := existsParam.(bool)
		if !ok {
			return nil, fmt.Errorf("parameter 'exists' must be a boolean")
		}

		info, err := os.Stat(folderPath)
		exists := err == nil && info.IsDir()

		if exists == shouldExist {
			result.Passed = true
			if shouldExist {
				result.Message = fmt.Sprintf("Folder %s exists as expected", folderName)
			} else {
				result.Message = fmt.Sprintf("Folder %s does not exist as expected", folderName)
			}
		} else {
			result.Passed = false
			if shouldExist {
				result.Message = fmt.Sprintf("Folder %s was expected to exist but was not found", folderName)
			} else {
				result.Message = fmt.Sprintf("Folder %s was not expected to exist but was found", folderName)
			}
		}
		result.Details = append(result.Details, fmt.Sprintf("Path checked: %s", folderPath))
	}

	// Check if folder contains files
	containsFilesParam, hasContainsFiles := validation.Parameters["contains_files"]
	if hasContainsFiles && result.Passed {
		containsFiles, ok := containsFilesParam.([]interface{})
		if !ok {
			return nil, fmt.Errorf("parameter 'contains_files' must be an array")
		}

		for _, fileInterface := range containsFiles {
			fileName, ok := fileInterface.(string)
			if !ok {
				return nil, fmt.Errorf("items in 'contains_files' must be strings")
			}

			filePath := filepath.Join(folderPath, fileName)
			if _, err := os.Stat(filePath); err != nil {
				result.Passed = false
				result.Message = fmt.Sprintf("Folder %s does not contain expected file: %s", folderName, fileName)
				result.Details = append(result.Details, fmt.Sprintf("Missing file: %s", filePath))
				break
			} else {
				result.Details = append(result.Details, fmt.Sprintf("Found expected file: %s", fileName))
			}
		}

		if result.Passed && len(containsFiles) > 0 {
			result.Message = fmt.Sprintf("Folder %s contains all expected files", folderName)
		}
	}

	// Check minimum file count
	minFilesParam, hasMinFiles := validation.Parameters["min_files"]
	if hasMinFiles && result.Passed {
		minFiles, ok := minFilesParam.(float64) // JSON numbers are float64
		if !ok {
			return nil, fmt.Errorf("parameter 'min_files' must be a number")
		}

		entries, err := os.ReadDir(folderPath)
		if err != nil {
			result.Passed = false
			result.Message = fmt.Sprintf("Failed to read folder %s: %v", folderName, err)
			return result, nil
		}

		fileCount := 0
		for _, entry := range entries {
			if !entry.IsDir() {
				fileCount++
			}
		}

		if fileCount >= int(minFiles) {
			result.Details = append(result.Details, fmt.Sprintf("Folder contains %d files (minimum %d required)", fileCount, int(minFiles)))
		} else {
			result.Passed = false
			result.Message = fmt.Sprintf("Folder %s contains %d files but minimum %d required", folderName, fileCount, int(minFiles))
		}
	}

	// If no specific checks were requested, just check that the folder exists
	if !hasExists && !hasContainsFiles && !hasMinFiles {
		info, err := os.Stat(folderPath)
		if err == nil && info.IsDir() {
			result.Passed = true
			result.Message = fmt.Sprintf("Folder %s exists", folderName)
		} else {
			result.Passed = false
			result.Message = fmt.Sprintf("Folder %s not found or is not a directory", folderName)
		}
	}

	return result, nil
}