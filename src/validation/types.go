package validation

import (
	"github.com/pboueri/intentc/src"
)

// ValidationTypeInfo contains metadata about a validation type
type ValidationTypeInfo struct {
	Type        src.ValidationType
	Description string
	Example     string
	Category    string // e.g., "file", "system", "ai-powered"
}

// ValidationTypeRegistry holds all registered validation types
var ValidationTypeRegistry = []ValidationTypeInfo{
	{
		Type:        src.ValidationTypeFileCheck,
		Description: "Validates file existence and content",
		Category:    "file",
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
		Category:    "file",
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
		Category:    "system",
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
		Category:    "ai-powered",
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
		Category:    "ai-powered",
		Example: `## Project structure valid
Type: ProjectCheck

### Parameters
- check: Verify the project follows Go module structure with go.mod and proper package organization

### Description
Uses AI to validate the overall project structure and setup`,
	},
}

// GetValidationTypeInfo returns info for a specific validation type
func GetValidationTypeInfo(valType src.ValidationType) *ValidationTypeInfo {
	for _, info := range ValidationTypeRegistry {
		if info.Type == valType {
			return &info
		}
	}
	return nil
}

// GetValidationTypes returns all registered validation types
func GetValidationTypes() []ValidationTypeInfo {
	return ValidationTypeRegistry
}

// GetValidationTypesByCategory returns validation types filtered by category
func GetValidationTypesByCategory(category string) []ValidationTypeInfo {
	var filtered []ValidationTypeInfo
	for _, info := range ValidationTypeRegistry {
		if info.Category == category {
			filtered = append(filtered, info)
		}
	}
	return filtered
}

// GenerateValidationTemplate generates a template for a specific validation type
func GenerateValidationTemplate(targetName string, valType src.ValidationType) string {
	info := GetValidationTypeInfo(valType)
	if info == nil {
		return ""
	}
	
	// Customize the example for the target
	template := info.Example
	
	// Simple template substitution - in a real implementation, we might use text/template
	switch valType {
	case src.ValidationTypeFileCheck:
		return `## Check ` + targetName + ` main file
Type: FileCheck

### Parameters
- file: src/main.go
- exists: true
- contains: package main

### Description
Ensures the main source file exists and contains the expected package declaration
`
	case src.ValidationTypeFolderCheck:
		return `## Check ` + targetName + ` directory structure
Type: FolderCheck

### Parameters
- folder: src
- exists: true
- min_files: 1

### Description
Ensures the source directory exists and contains at least one file
`
	case src.ValidationTypeCommandLineCheck:
		return `## ` + targetName + ` tests pass
Type: CommandLineCheck

### Parameters
- command: go test ./...
- exit_code: 0
- output_contains: PASS

### Description
Runs the test suite and ensures all tests pass
`
	case src.ValidationTypeWebCheck:
		return `## ` + targetName + ` web service running
Type: WebCheck

### Parameters
- url: http://localhost:8080
- check: Verify the web service is running and responds with a 200 status code

### Description
Uses AI to check if the web service is accessible and functioning
`
	case src.ValidationTypeProjectCheck:
		return `## ` + targetName + ` project structure valid
Type: ProjectCheck

### Parameters
- check: Verify the project follows proper structure with required dependencies and configuration files

### Description
Uses AI to validate the overall project setup and organization
`
	default:
		return template
	}
}