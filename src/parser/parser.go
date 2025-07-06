package parser

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/pboueri/intentc/src"
)

type Parser struct {
	intentParser     *IntentParser
	validationParser *ValidationParser
}

func New() *Parser {
	return &Parser{
		intentParser:     NewIntentParser(),
		validationParser: NewValidationParser(),
	}
}

func (p *Parser) ParseIntentDirectory(intentDir string) ([]string, error) {
	entries, err := os.ReadDir(intentDir)
	if err != nil {
		return nil, err
	}

	var features []string
	for _, entry := range entries {
		if entry.IsDir() {
			// Check if it contains any .ic file
			dirPath := filepath.Join(intentDir, entry.Name())
			if p.hasIntentFile(dirPath) {
				features = append(features, entry.Name())
			}
		}
	}

	return features, nil
}

// hasIntentFile checks if a directory contains at least one .ic file
func (p *Parser) hasIntentFile(dirPath string) bool {
	entries, err := os.ReadDir(dirPath)
	if err != nil {
		return false
	}

	for _, entry := range entries {
		if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".ic") {
			return true
		}
	}
	return false
}

// FindIntentFile finds the .ic file in a directory (returns error if none or multiple found)
func (p *Parser) FindIntentFile(dirPath string) (string, error) {
	entries, err := os.ReadDir(dirPath)
	if err != nil {
		return "", err
	}

	var intentFiles []string
	for _, entry := range entries {
		if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".ic") {
			intentFiles = append(intentFiles, filepath.Join(dirPath, entry.Name()))
		}
	}

	switch len(intentFiles) {
	case 0:
		return "", fmt.Errorf("no .ic file found in %s", dirPath)
	case 1:
		return intentFiles[0], nil
	default:
		return "", fmt.Errorf("multiple .ic files found in %s: %v", dirPath, intentFiles)
	}
}

func (p *Parser) ParseIntentFile(filePath string) (*src.Intent, error) {
	return p.intentParser.ParseIntent(filePath)
}

func (p *Parser) ParseValidationFiles(featureDir string) ([]*src.ValidationFile, error) {
	entries, err := os.ReadDir(featureDir)
	if err != nil {
		return nil, err
	}

	var validationFiles []*src.ValidationFile
	for _, entry := range entries {
		if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".icv") {
			filePath := filepath.Join(featureDir, entry.Name())
			validations, err := p.validationParser.ParseValidationFile(filePath)
			if err != nil {
				return nil, err
			}
			validationFiles = append(validationFiles, validations)
		}
	}

	return validationFiles, nil
}