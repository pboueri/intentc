package parser

import (
	"bufio"
	"os"
	"path/filepath"
	"strings"
)

// MarkdownSection represents a section in a markdown document
type MarkdownSection struct {
	Level   int    // 1 for #, 2 for ##, 3 for ###, etc.
	Title   string
	Content string
}

// MarkdownDocument represents a parsed markdown document
type MarkdownDocument struct {
	Sections []MarkdownSection
	Metadata map[string]string // For key-value pairs like "Type: FileCheck"
}

// ParseMarkdownFile reads and parses a markdown file into sections
func ParseMarkdownFile(filePath string) (*MarkdownDocument, error) {
	content, err := os.ReadFile(filePath)
	if err != nil {
		return nil, err
	}
	return ParseMarkdown(string(content)), nil
}

// ParseMarkdown parses markdown content into sections
func ParseMarkdown(content string) *MarkdownDocument {
	doc := &MarkdownDocument{
		Sections: []MarkdownSection{},
		Metadata: make(map[string]string),
	}

	scanner := bufio.NewScanner(strings.NewReader(content))
	var currentSection *MarkdownSection
	var sectionContent strings.Builder

	for scanner.Scan() {
		line := scanner.Text()
		trimmed := strings.TrimSpace(line)

		// Check for headers
		headerLevel := getHeaderLevel(line)
		if headerLevel > 0 {
			// Save previous section if exists
			if currentSection != nil {
				currentSection.Content = strings.TrimSpace(sectionContent.String())
				doc.Sections = append(doc.Sections, *currentSection)
				sectionContent.Reset()
			}

			// Start new section
			currentSection = &MarkdownSection{
				Level: headerLevel,
				Title: strings.TrimSpace(line[headerLevel+1:]), // Skip "# " or "## " etc.
			}
		} else if currentSection != nil {
			// Check for metadata lines (key: value)
			if idx := strings.Index(trimmed, ":"); idx > 0 && !strings.Contains(trimmed[:idx], " ") {
				key := trimmed[:idx]
				value := strings.TrimSpace(trimmed[idx+1:])
				doc.Metadata[key] = value
			}
			sectionContent.WriteString(line + "\n")
		}
	}

	// Save last section
	if currentSection != nil {
		currentSection.Content = strings.TrimSpace(sectionContent.String())
		doc.Sections = append(doc.Sections, *currentSection)
	}

	return doc
}

// getHeaderLevel returns the header level (1 for #, 2 for ##, etc.) or 0 if not a header
func getHeaderLevel(line string) int {
	if !strings.HasPrefix(line, "#") {
		return 0
	}

	level := 0
	for i, ch := range line {
		if ch == '#' {
			level++
		} else if ch == ' ' {
			break
		} else {
			// Not a valid header (e.g., "#notaheader")
			return 0
		}
		if i > 5 { // Max header level is 6
			return 0
		}
	}
	return level
}

// ParseKeyValueList parses a list format like:
// - key1: value1
// - key2: value2
func ParseKeyValueList(content string) map[string]interface{} {
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
			
			// Convert boolean strings
			if value == "true" || value == "false" {
				params[key] = value == "true"
			} else {
				params[key] = value
			}
		}
	}
	
	return params
}

// ParseCommaSeparatedList parses comma-separated values
func ParseCommaSeparatedList(content string) []string {
	var items []string
	if content == "" {
		return items
	}
	
	parts := strings.Split(content, ",")
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			items = append(items, part)
		}
	}
	
	return items
}

// FindFilesByExtension walks a directory and finds all files with the given extension
func FindFilesByExtension(dir string, extension string) ([]string, error) {
	var files []string
	
	err := filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if !info.IsDir() && strings.HasSuffix(path, extension) {
			files = append(files, path)
		}
		return nil
	})
	
	return files, err
}

// GetSectionByTitle finds a section by its title (case-insensitive)
func (doc *MarkdownDocument) GetSectionByTitle(title string) *MarkdownSection {
	lowerTitle := strings.ToLower(title)
	for i := range doc.Sections {
		if strings.ToLower(doc.Sections[i].Title) == lowerTitle {
			return &doc.Sections[i]
		}
	}
	return nil
}

// GetSectionsByLevel returns all sections at a specific header level
func (doc *MarkdownDocument) GetSectionsByLevel(level int) []MarkdownSection {
	var sections []MarkdownSection
	for _, section := range doc.Sections {
		if section.Level == level {
			sections = append(sections, section)
		}
	}
	return sections
}