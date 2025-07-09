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
	
	// Get all level 2 sections (##) which represent individual validations
	level2Sections := doc.GetSectionsByLevel(2)
	
	for _, section := range level2Sections {
		validation := src.Validation{
			Name:       section.Title,
			Parameters: make(map[string]interface{}),
		}
		
		// Parse the content of this validation section
		lines := strings.Split(section.Content, "\n")
		var currentSubsection string
		var subsectionContent strings.Builder
		
		for _, line := range lines {
			trimmed := strings.TrimSpace(line)
			
			// Check for Type and Hidden metadata
			if strings.HasPrefix(trimmed, "Type:") {
				validation.Type = src.ValidationType(strings.TrimSpace(strings.TrimPrefix(trimmed, "Type:")))
			} else if strings.HasPrefix(trimmed, "Hidden:") {
				hiddenStr := strings.TrimSpace(strings.TrimPrefix(trimmed, "Hidden:"))
				validation.Hidden = hiddenStr == "true"
			} else if strings.HasPrefix(line, "### ") {
				// Process previous subsection
				if currentSubsection == "parameters" {
					validation.Parameters = ParseKeyValueList(subsectionContent.String())
				} else if currentSubsection == "description" {
					validation.Description = strings.TrimSpace(subsectionContent.String())
				}
				
				// Start new subsection
				currentSubsection = strings.ToLower(strings.TrimSpace(strings.TrimPrefix(line, "### ")))
				subsectionContent.Reset()
			} else if currentSubsection != "" {
				subsectionContent.WriteString(line + "\n")
			}
		}
		
		// Process last subsection
		if currentSubsection == "parameters" {
			validation.Parameters = ParseKeyValueList(subsectionContent.String())
		} else if currentSubsection == "description" {
			validation.Description = strings.TrimSpace(subsectionContent.String())
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
