package repl

import (
	"fmt"
	"strings"
	"time"

	"github.com/pboueri/intentc/src"
)

// ReplContext maintains the state and history of the REPL session
type ReplContext struct {
	target         *src.Target
	projectRoot    string
	generatedFiles map[string]bool
	fileContexts   map[string]string
	history        []HistoryEntry
}

// HistoryEntry represents a single refinement in the history
type HistoryEntry struct {
	Timestamp time.Time
	Prompt    string
	Response  string
	Files     []string
}

// NewReplContext creates a new REPL context
func NewReplContext(target *src.Target, projectRoot string) *ReplContext {
	return &ReplContext{
		target:         target,
		projectRoot:    projectRoot,
		generatedFiles: make(map[string]bool),
		fileContexts:   make(map[string]string),
		history:        []HistoryEntry{},
	}
}

// AddGeneratedFile adds a file to the list of generated files
func (c *ReplContext) AddGeneratedFile(file string) {
	c.generatedFiles[file] = true
}

// GetGeneratedFiles returns all generated files
func (c *ReplContext) GetGeneratedFiles() []string {
	files := make([]string, 0, len(c.generatedFiles))
	for file := range c.generatedFiles {
		files = append(files, file)
	}
	return files
}

// AddFileContext adds a file to the refinement context
func (c *ReplContext) AddFileContext(file string, content string) {
	c.fileContexts[file] = content
}

// ClearFileContexts clears all file contexts
func (c *ReplContext) ClearFileContexts() {
	c.fileContexts = make(map[string]string)
}

// AddRefinement adds a refinement to the history
func (c *ReplContext) AddRefinement(prompt string, response string) {
	entry := HistoryEntry{
		Timestamp: time.Now(),
		Prompt:    prompt,
		Response:  response,
		Files:     c.GetGeneratedFiles(),
	}
	c.history = append(c.history, entry)
}

// UpdateRefinementResponse updates the response for the last refinement
func (c *ReplContext) UpdateRefinementResponse(prompt string, response string) {
	if len(c.history) > 0 && c.history[len(c.history)-1].Prompt == prompt {
		c.history[len(c.history)-1].Response = response
	}
}

// GetHistory returns the refinement history
func (c *ReplContext) GetHistory() []HistoryEntry {
	return c.history
}

// BuildRefinementPrompt builds a complete refinement prompt with context
func (c *ReplContext) BuildRefinementPrompt(userPrompt string) string {
	var prompt strings.Builder

	// Add target context
	prompt.WriteString(fmt.Sprintf("REFINEMENT REQUEST\n"))
	prompt.WriteString(fmt.Sprintf("Target: %s\n\n", c.target.Name))

	// Add original intent
	prompt.WriteString("Original Intent:\n")
	prompt.WriteString(c.target.Intent.Content)
	prompt.WriteString("\n\n")

	// Add file contexts if any
	if len(c.fileContexts) > 0 {
		prompt.WriteString("Current File Context:\n")
		for file, content := range c.fileContexts {
			prompt.WriteString(fmt.Sprintf("\n=== %s ===\n", file))
			// Limit content length to avoid overwhelming the agent
			if len(content) > 1000 {
				prompt.WriteString(content[:1000])
				prompt.WriteString("\n... (truncated)")
			} else {
				prompt.WriteString(content)
			}
			prompt.WriteString("\n=== END ===\n")
		}
		prompt.WriteString("\n")
	}

	// Add conversation history (last 3 refinements)
	if len(c.history) > 0 {
		prompt.WriteString("Recent Refinements:\n")
		start := len(c.history) - 3
		if start < 0 {
			start = 0
		}
		for i := start; i < len(c.history); i++ {
			h := c.history[i]
			prompt.WriteString(fmt.Sprintf("- %s: %s", h.Timestamp.Format("15:04"), h.Prompt))
			if h.Response != "" {
				prompt.WriteString(fmt.Sprintf(" -> %s", h.Response))
			}
			prompt.WriteString("\n")
		}
		prompt.WriteString("\n")
	}

	// Add user's refinement request
	prompt.WriteString("User Request:\n")
	prompt.WriteString(userPrompt)
	prompt.WriteString("\n\n")

	// Add instructions
	prompt.WriteString("Please refine the implementation based on the user's feedback. ")
	prompt.WriteString("Maintain consistency with the original intent while addressing the requested changes. ")
	prompt.WriteString("If the refinement suggests changes to the intent itself, indicate what intent updates would be needed.\n")

	return prompt.String()
}

// SaveSession saves the REPL session to a file
func (c *ReplContext) SaveSession(filename string) error {
	// Implementation for saving session
	// This could be implemented later for session persistence
	return fmt.Errorf("session save not yet implemented")
}

// LoadSession loads a REPL session from a file
func (c *ReplContext) LoadSession(filename string) error {
	// Implementation for loading session
	// This could be implemented later for session persistence
	return fmt.Errorf("session load not yet implemented")
}