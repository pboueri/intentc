package parser

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/pboueri/intentc/src"
)

type IntentParser struct{}

func NewIntentParser() *IntentParser {
	return &IntentParser{}
}

func (p *IntentParser) ParseIntent(filePath string) (*src.Intent, error) {
	content, err := os.ReadFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("failed to read intent file: %w", err)
	}

	intent := &src.Intent{
		FilePath:     filePath,
		Name:         filepath.Base(filepath.Dir(filePath)),
		Dependencies: []string{},
		Content:      string(content),
	}

	// Parse dependencies using simple line-by-line approach for compatibility
	lines := strings.Split(string(content), "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		
		// Handle "Depends On:" format
		if strings.HasPrefix(line, "Depends On:") {
			depLine := strings.TrimSpace(strings.TrimPrefix(line, "Depends On:"))
			if depLine != "" {
				intent.Dependencies = ParseCommaSeparatedList(depLine)
			}
		}
	}

	return intent, nil
}

func contains(slice []string, item string) bool {
	for _, s := range slice {
		if s == item {
			return true
		}
	}
	return false
}

func (p *IntentParser) ParseProjectIntent(filePath string) (*src.Intent, error) {
	content, err := os.ReadFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("failed to read project intent file: %w", err)
	}

	return &src.Intent{
		FilePath: filePath,
		Name:     "project",
		Content:  string(content),
	}, nil
}

func (p *IntentParser) FindIntentFiles(intentDir string) ([]string, error) {
	intentFiles, err := FindFilesByExtension(intentDir, ".ic")
	if err != nil {
		return nil, fmt.Errorf("failed to find intent files: %w", err)
	}
	return intentFiles, nil
}
