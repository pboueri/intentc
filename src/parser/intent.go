package parser

import (
	"bufio"
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

	scanner := bufio.NewScanner(strings.NewReader(string(content)))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		
		// Only parse dependencies, don't override the name
		if strings.HasPrefix(line, "Depends On:") {
			depLine := strings.TrimPrefix(line, "Depends On:")
			deps := strings.Split(depLine, ",")
			for _, dep := range deps {
				dep = strings.TrimSpace(dep)
				if dep != "" {
					intent.Dependencies = append(intent.Dependencies, dep)
				}
			}
		}
	}

	return intent, nil
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
	var intentFiles []string

	err := filepath.Walk(intentDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if !info.IsDir() && strings.HasSuffix(path, ".ic") {
			intentFiles = append(intentFiles, path)
		}
		return nil
	})

	if err != nil {
		return nil, fmt.Errorf("failed to walk intent directory: %w", err)
	}

	return intentFiles, nil
}
