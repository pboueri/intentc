package parser

import (
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
			// Check if it contains an .ic file
			icFile := filepath.Join(intentDir, entry.Name(), entry.Name()+".ic")
			if _, err := os.Stat(icFile); err == nil {
				features = append(features, entry.Name())
			}
		}
	}

	return features, nil
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