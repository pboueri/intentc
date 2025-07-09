package intent

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/pboueri/intentc/src"
)

type Parser struct{}

func NewParser() *Parser {
	return &Parser{}
}

type IntentType string

const (
	IntentTypeFeature IntentType = "feature"
	IntentTypeProject IntentType = "project"
)

type IntentFile struct {
	Path         string
	Type         IntentType
	Name         string
	Dependencies []string
	Targets      []Target
	Description  string
	UserExperience string
	QualityGoals string
	RawContent   string
}

type Target struct {
	Name        string
	Description string
	Content     string
}

func (p *Parser) ParseIntentFile(filePath string) (*IntentFile, error) {
	content, err := os.ReadFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("failed to read intent file: %w", err)
	}

	intent := &IntentFile{
		Path:         filePath,
		Dependencies: []string{},
		Targets:      []Target{},
		RawContent:   string(content),
	}

	scanner := bufio.NewScanner(strings.NewReader(string(content)))
	currentSection := ""
	var sectionContent strings.Builder
	inCodeBlock := false
	
	for scanner.Scan() {
		line := scanner.Text()
		trimmed := strings.TrimSpace(line)
		
		// Handle code blocks
		if strings.HasPrefix(trimmed, "```") {
			inCodeBlock = !inCodeBlock
			if currentSection != "" {
				sectionContent.WriteString(line + "\n")
			}
			continue
		}
		
		// Skip processing headers inside code blocks
		if inCodeBlock {
			if currentSection != "" {
				sectionContent.WriteString(line + "\n")
			}
			continue
		}
		
		// Parse main header
		if strings.HasPrefix(line, "# ") {
			p.processSection(intent, currentSection, sectionContent.String())
			sectionContent.Reset()
			
			header := strings.TrimPrefix(line, "# ")
			if strings.HasPrefix(header, "Feature:") {
				intent.Type = IntentTypeFeature
				intent.Name = strings.TrimSpace(strings.TrimPrefix(header, "Feature:"))
			} else if strings.HasPrefix(header, "Project:") {
				intent.Type = IntentTypeProject
				intent.Name = strings.TrimSpace(strings.TrimPrefix(header, "Project:"))
			} else {
				intent.Name = header
				intent.Type = IntentTypeFeature // default
			}
			currentSection = ""
			continue
		}
		
		// Check for inline "Depends On:" immediately after header
		if currentSection == "" && strings.HasPrefix(trimmed, "Depends On:") {
			depLine := strings.TrimPrefix(trimmed, "Depends On:")
			intent.Dependencies = p.parseDependencies(depLine)
			continue
		}
		
		// Parse section headers
		if strings.HasPrefix(line, "## ") {
			p.processSection(intent, currentSection, sectionContent.String())
			sectionContent.Reset()
			
			currentSection = strings.TrimSpace(strings.TrimPrefix(line, "## "))
			continue
		}
		
		// Accumulate section content
		if currentSection != "" {
			sectionContent.WriteString(line + "\n")
		}
	}
	
	// Process last section
	p.processSection(intent, currentSection, sectionContent.String())
	
	if intent.Name == "" {
		intent.Name = strings.TrimSuffix(filepath.Base(filePath), ".ic")
	}
	
	return intent, nil
}

func (p *Parser) processSection(intent *IntentFile, section, content string) {
	content = strings.TrimSpace(content)
	if content == "" && section != "Dependencies" {
		return
	}
	
	switch strings.ToLower(section) {
	case "dependencies":
		intent.Dependencies = p.parseDependencies(content)
	case "description":
		intent.Description = content
	case "user experience":
		intent.UserExperience = content
	case "quality goals":
		intent.QualityGoals = content
	default:
		if strings.HasPrefix(section, "Target:") {
			targetName := strings.TrimSpace(strings.TrimPrefix(section, "Target:"))
			intent.Targets = append(intent.Targets, Target{
				Name:    targetName,
				Content: content,
			})
		}
	}
}

func (p *Parser) parseDependencies(content string) []string {
	var deps []string
	content = strings.TrimSpace(content)
	
	// If content is empty, return empty dependencies
	if content == "" {
		return deps
	}
	
	// If content is just "Depends On:", return empty dependencies
	if content == "Depends On:" {
		return deps
	}
	
	// If content starts with "Depends On:", parse what comes after
	if strings.HasPrefix(content, "Depends On:") {
		content = strings.TrimSpace(strings.TrimPrefix(content, "Depends On:"))
		if content == "" {
			return deps
		}
	}
	
	scanner := bufio.NewScanner(strings.NewReader(content))
	
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(line, "-") {
			dep := strings.TrimSpace(strings.TrimPrefix(line, "-"))
			if dep != "" {
				deps = append(deps, dep)
			}
		} else if line != "" && !strings.HasPrefix(line, "#") && line != "Depends On:" {
			// Handle comma-separated dependencies (from "Depends On:" format)
			parts := strings.Split(line, ",")
			for _, part := range parts {
				dep := strings.TrimSpace(part)
				if dep != "" && dep != "Depends On:" {
					deps = append(deps, dep)
				}
			}
		}
	}
	
	return deps
}

func (p *Parser) DiscoverIntentFiles(rootDir string) ([]*IntentFile, error) {
	var intents []*IntentFile
	
	// Check for project-level intents
	projectDir := filepath.Join(rootDir, "intent", "project")
	if info, err := os.Stat(projectDir); err == nil && info.IsDir() {
		projectIntents, err := p.discoverInDirectory(projectDir)
		if err != nil {
			return nil, fmt.Errorf("failed to discover project intents: %w", err)
		}
		intents = append(intents, projectIntents...)
	}
	
	// Check for feature intents
	featuresDir := filepath.Join(rootDir, "intent", "features")
	if info, err := os.Stat(featuresDir); err == nil && info.IsDir() {
		entries, err := os.ReadDir(featuresDir)
		if err != nil {
			return nil, fmt.Errorf("failed to read features directory: %w", err)
		}
		
		for _, entry := range entries {
			if entry.IsDir() {
				featureDir := filepath.Join(featuresDir, entry.Name())
				featureIntents, err := p.discoverInDirectory(featureDir)
				if err != nil {
					return nil, fmt.Errorf("failed to discover intents in %s: %w", entry.Name(), err)
				}
				intents = append(intents, featureIntents...)
			}
		}
	}
	
	// Also check the standard intent directory structure
	intentDir := filepath.Join(rootDir, "intent")
	entries, err := os.ReadDir(intentDir)
	if err == nil {
		for _, entry := range entries {
			if entry.IsDir() && entry.Name() != "project" && entry.Name() != "features" {
				featureDir := filepath.Join(intentDir, entry.Name())
				featureIntents, err := p.discoverInDirectory(featureDir)
				if err != nil {
					return nil, fmt.Errorf("failed to discover intents in %s: %w", entry.Name(), err)
				}
				intents = append(intents, featureIntents...)
			}
		}
	}
	
	return intents, nil
}

func (p *Parser) discoverInDirectory(dir string) ([]*IntentFile, error) {
	var intents []*IntentFile
	
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	
	// Count .ic files to ensure only one per directory
	var icFiles []string
	for _, entry := range entries {
		if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".ic") {
			icFiles = append(icFiles, entry.Name())
		}
	}
	
	if len(icFiles) == 0 {
		// No .ic file in directory, skip silently
		return intents, nil
	}
	
	if len(icFiles) > 1 {
		return nil, fmt.Errorf("multiple .ic files found in %s: %v", dir, icFiles)
	}
	
	// Parse the single .ic file
	filePath := filepath.Join(dir, icFiles[0])
	intent, err := p.ParseIntentFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("failed to parse %s: %w", filePath, err)
	}
	
	// Override the name with the directory name
	intent.Name = filepath.Base(dir)
	
	intents = append(intents, intent)
	
	return intents, nil
}

// ConvertToLegacyIntent converts the new IntentFile format to the legacy src.Intent format
func (p *Parser) ConvertToLegacyIntent(intentFile *IntentFile) *src.Intent {
	content := intentFile.RawContent
	
	// If we have structured content, rebuild it
	if intentFile.Description != "" || intentFile.UserExperience != "" || intentFile.QualityGoals != "" {
		var builder strings.Builder
		builder.WriteString(fmt.Sprintf("# %s: %s\n\n", intentFile.Type, intentFile.Name))
		
		if len(intentFile.Dependencies) > 0 {
			builder.WriteString("## Dependencies\n")
			for _, dep := range intentFile.Dependencies {
				builder.WriteString(fmt.Sprintf("- %s\n", dep))
			}
			builder.WriteString("\n")
		}
		
		if intentFile.Description != "" {
			builder.WriteString("## Description\n")
			builder.WriteString(intentFile.Description)
			builder.WriteString("\n\n")
		}
		
		if intentFile.UserExperience != "" {
			builder.WriteString("## User Experience\n")
			builder.WriteString(intentFile.UserExperience)
			builder.WriteString("\n\n")
		}
		
		if intentFile.QualityGoals != "" {
			builder.WriteString("## Quality Goals\n")
			builder.WriteString(intentFile.QualityGoals)
			builder.WriteString("\n\n")
		}
		
		for _, target := range intentFile.Targets {
			builder.WriteString(fmt.Sprintf("## Target: %s\n", target.Name))
			builder.WriteString(target.Content)
			builder.WriteString("\n\n")
		}
		
		content = builder.String()
	}
	
	return &src.Intent{
		Name:         intentFile.Name,
		Dependencies: intentFile.Dependencies,
		Content:      content,
		FilePath:     intentFile.Path,
	}
}