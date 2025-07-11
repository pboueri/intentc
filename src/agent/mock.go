package agent

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"

	"github.com/pboueri/intentc/src"
)

type MockAgent struct {
	Name          string
	BuildFunc     func(ctx context.Context, buildCtx BuildContext) ([]string, error)
	RefineFunc    func(ctx context.Context, target *src.Target, prompt string) error
	ValidateFunc  func(ctx context.Context, validation *src.Validation, generatedFiles []string) (bool, string, error)
	DecompileFunc func(ctx context.Context, decompileCtx DecompileContext) ([]string, error)
}

func NewMockAgent(name string) *MockAgent {
	return &MockAgent{
		Name: name,
	}
}

func (m *MockAgent) Build(ctx context.Context, buildCtx BuildContext) ([]string, error) {
	if m.BuildFunc != nil {
		return m.BuildFunc(ctx, buildCtx)
	}
	
	// Default implementation for testing
	// Parse the intent to extract file creation instructions
	intentContent := buildCtx.Intent.Content
	
	// Look for patterns like "Create a file named X.txt" and "containing the keyword Y"
	fileNameRegex := regexp.MustCompile(`(?i)create\s+a\s+file\s+named\s+(\S+\.txt)`)
	keywordRegex := regexp.MustCompile(`(?i)containing\s+the\s+keyword\s+"([^"]+)"`)
	
	fileNameMatch := fileNameRegex.FindStringSubmatch(intentContent)
	keywordMatch := keywordRegex.FindStringSubmatch(intentContent)
	
	if len(fileNameMatch) > 1 && len(keywordMatch) > 1 {
		fileName := fileNameMatch[1]
		keyword := keywordMatch[1]
		
		// Create the file
		filePath := filepath.Join(buildCtx.ProjectRoot, fileName)
		err := os.WriteFile(filePath, []byte(keyword), 0644)
		if err != nil {
			return nil, fmt.Errorf("failed to create file: %w", err)
		}
		
		// Return relative path
		return []string{fileName}, nil
	}
	
	// Fallback: create a default file
	fileName := fmt.Sprintf("%s.txt", buildCtx.Intent.Name)
	filePath := filepath.Join(buildCtx.ProjectRoot, fileName)
	content := fmt.Sprintf("Generated content for %s", buildCtx.Intent.Name)
	
	err := os.WriteFile(filePath, []byte(content), 0644)
	if err != nil {
		return nil, fmt.Errorf("failed to create file: %w", err)
	}
	
	// Return relative path
	return []string{fileName}, nil
}

func (m *MockAgent) Refine(ctx context.Context, target *src.Target, prompt string) error {
	if m.RefineFunc != nil {
		return m.RefineFunc(ctx, target, prompt)
	}
	return nil
}

func (m *MockAgent) Validate(ctx context.Context, validation *src.Validation, generatedFiles []string) (bool, string, error) {
	if m.ValidateFunc != nil {
		return m.ValidateFunc(ctx, validation, generatedFiles)
	}
	
	// For testing purposes, simply return success
	// The actual validation is done by the validation system itself
	return true, fmt.Sprintf("Mock validation passed for %s", validation.Name), nil
}

func (m *MockAgent) GetName() string {
	return m.Name
}

func (m *MockAgent) GetType() string {
	return "mock"
}

func (m *MockAgent) Decompile(ctx context.Context, decompileCtx DecompileContext) ([]string, error) {
	if m.DecompileFunc != nil {
		return m.DecompileFunc(ctx, decompileCtx)
	}
	
	// Default implementation for testing
	// Create a simple project.ic file
	projectIC := filepath.Join(decompileCtx.OutputPath, "project.ic")
	content := `# Project: Mock Project

## Overview
This is a mock project for testing decompile functionality.

## Features
- feature-mock: Mock feature for testing
`
	
	err := os.MkdirAll(decompileCtx.OutputPath, 0755)
	if err != nil {
		return nil, fmt.Errorf("failed to create output directory: %w", err)
	}
	
	err = os.WriteFile(projectIC, []byte(content), 0644)
	if err != nil {
		return nil, fmt.Errorf("failed to create project.ic: %w", err)
	}
	
	return []string{projectIC}, nil
}

type MockAgentFactory struct{}

func NewMockAgentFactory() *MockAgentFactory {
	return &MockAgentFactory{}
}

func (f *MockAgentFactory) CreateAgent(config src.Agent) (Agent, error) {
	return NewMockAgent("mock-agent"), nil
}

func (f *MockAgentFactory) GetSupportedTypes() []string {
	return []string{"mock"}
}
