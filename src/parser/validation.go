package parser

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/pboueri/intentc/src"
)

type ValidationParser struct{}

func NewValidationParser() *ValidationParser {
	return &ValidationParser{}
}

func (p *ValidationParser) ParseValidationFile(filePath string) (*src.ValidationFile, error) {
	content, err := os.ReadFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("failed to read validation file: %w", err)
	}

	validationFile := &src.ValidationFile{
		FilePath:    filePath,
		Validations: []src.Validation{},
	}

	validations, err := p.parseMarkdownValidations(string(content))
	if err != nil {
		return nil, fmt.Errorf("failed to parse validations: %w", err)
	}

	validationFile.Validations = validations
	return validationFile, nil
}

func (p *ValidationParser) parseMarkdownValidations(content string) ([]src.Validation, error) {
	var validations []src.Validation
	scanner := bufio.NewScanner(strings.NewReader(content))
	
	var currentValidation *src.Validation
	var currentSection string
	var sectionContent strings.Builder
	
	for scanner.Scan() {
		line := scanner.Text()
		trimmedLine := strings.TrimSpace(line)
		
		if strings.HasPrefix(line, "## ") && !strings.HasPrefix(line, "### ") {
			if currentValidation != nil {
				if currentSection == "parameters" {
					params, err := p.parseParameters(sectionContent.String())
					if err != nil {
						return nil, err
					}
					currentValidation.Parameters = params
				} else if currentSection == "description" {
					currentValidation.Description = strings.TrimSpace(sectionContent.String())
				}
				validations = append(validations, *currentValidation)
			}
			
			currentValidation = &src.Validation{
				Name:       strings.TrimPrefix(line, "## "),
				Parameters: make(map[string]interface{}),
			}
			currentSection = ""
			sectionContent.Reset()
		} else if strings.HasPrefix(line, "### ") {
			if currentSection == "parameters" {
				params, err := p.parseParameters(sectionContent.String())
				if err != nil {
					return nil, err
				}
				currentValidation.Parameters = params
			} else if currentSection == "description" {
				currentValidation.Description = strings.TrimSpace(sectionContent.String())
			}
			
			section := strings.ToLower(strings.TrimPrefix(line, "### "))
			currentSection = section
			sectionContent.Reset()
		} else if strings.HasPrefix(trimmedLine, "Type:") {
			typeStr := strings.TrimSpace(strings.TrimPrefix(trimmedLine, "Type:"))
			currentValidation.Type = src.ValidationType(typeStr)
		} else if strings.HasPrefix(trimmedLine, "Hidden:") {
			hiddenStr := strings.TrimSpace(strings.TrimPrefix(trimmedLine, "Hidden:"))
			currentValidation.Hidden = hiddenStr == "true"
		} else if currentSection != "" {
			sectionContent.WriteString(line + "\n")
		}
	}
	
	if currentValidation != nil {
		if currentSection == "parameters" {
			params, err := p.parseParameters(sectionContent.String())
			if err != nil {
				return nil, err
			}
			currentValidation.Parameters = params
		} else if currentSection == "description" {
			currentValidation.Description = strings.TrimSpace(sectionContent.String())
		}
		validations = append(validations, *currentValidation)
	}
	
	return validations, nil
}

func (p *ValidationParser) parseParameters(content string) (map[string]interface{}, error) {
	params := make(map[string]interface{})
	
	lines := strings.Split(content, "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" || !strings.HasPrefix(line, "- ") {
			continue
		}
		
		line = strings.TrimPrefix(line, "- ")
		parts := strings.SplitN(line, ":", 2)
		if len(parts) == 2 {
			key := strings.TrimSpace(parts[0])
			value := strings.TrimSpace(parts[1])
			
			if value == "true" || value == "false" {
				params[key] = value == "true"
			} else {
				params[key] = value
			}
		}
	}
	
	return params, nil
}

func (p *ValidationParser) FindValidationFiles(intentDir string) ([]string, error) {
	var validationFiles []string

	err := filepath.Walk(intentDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if !info.IsDir() && strings.HasSuffix(path, ".icv") {
			validationFiles = append(validationFiles, path)
		}
		return nil
	})

	if err != nil {
		return nil, fmt.Errorf("failed to walk intent directory: %w", err)
	}

	return validationFiles, nil
}
