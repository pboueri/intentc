package parser

import (
	"fmt"
	"strings"

	"github.com/pboueri/intentc/src"
)

type ValidationParser struct{}

func NewValidationParser() *ValidationParser {
	return &ValidationParser{}
}

func (p *ValidationParser) ParseValidationFile(filePath string) (*src.ValidationFile, error) {
	doc, err := ParseMarkdownFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("failed to read validation file: %w", err)
	}

	validationFile := &src.ValidationFile{
		FilePath:    filePath,
		Validations: []src.Validation{},
	}

	validations, err := p.parseMarkdownValidations(doc)
	if err != nil {
		return nil, fmt.Errorf("failed to parse validations: %w", err)
	}

	validationFile.Validations = validations
	return validationFile, nil
}

func (p *ValidationParser) parseMarkdownValidations(doc *MarkdownDocument) ([]src.Validation, error) {
	var validations []src.Validation
	
	// Get all sections
	allSections := doc.Sections
	
	// Process level 2 sections as validations
	for i := 0; i < len(allSections); i++ {
		section := allSections[i]
		
		// Skip non-level-2 sections
		if section.Level != 2 {
			continue
		}
		
		validation := src.Validation{
			Name:       section.Title,
			Parameters: make(map[string]interface{}),
		}
		
		// Parse the content of this validation section for Type
		lines := strings.Split(section.Content, "\n")
		for _, line := range lines {
			trimmed := strings.TrimSpace(line)
			if strings.HasPrefix(trimmed, "Type:") {
				validation.Type = src.ValidationType(strings.TrimSpace(strings.TrimPrefix(trimmed, "Type:")))
			} else if strings.HasPrefix(trimmed, "Hidden:") {
				hiddenStr := strings.TrimSpace(strings.TrimPrefix(trimmed, "Hidden:"))
				validation.Hidden = hiddenStr == "true"
			}
		}
		
		// Look for level 3 subsections that follow this level 2 section
		for j := i + 1; j < len(allSections) && allSections[j].Level >= 3; j++ {
			if allSections[j].Level == 3 {
				subsectionTitle := strings.ToLower(allSections[j].Title)
				subsectionContent := allSections[j].Content
				
				if subsectionTitle == "parameters" {
					validation.Parameters = ParseKeyValueList(subsectionContent)
				} else if subsectionTitle == "description" {
					validation.Description = strings.TrimSpace(subsectionContent)
				}
			}
		}
		
		validations = append(validations, validation)
	}
	
	return validations, nil
}

func (p *ValidationParser) FindValidationFiles(intentDir string) ([]string, error) {
	validationFiles, err := FindFilesByExtension(intentDir, ".icv")
	if err != nil {
		return nil, fmt.Errorf("failed to find validation files: %w", err)
	}
	return validationFiles, nil
}
