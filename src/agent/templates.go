package agent

import "github.com/pboueri/intentc/src"

// PromptTemplates contains all the prompt templates used by agents
type PromptTemplates struct {
	Build    string
	Refine   string
	Validate string
}

// DefaultPromptTemplates returns the default prompt templates
var DefaultPromptTemplates = PromptTemplates{
	Build: `# Code Generation Request

Project: {{.ProjectRoot}}
Target: {{.IntentName}}
{{if .GenerationID}}Generation ID: {{.GenerationID}}{{end}}
{{if .Dependencies}}Dependencies: {{.Dependencies}}{{end}}

## Intent
{{.IntentContent}}

{{if .Validations}}
## Validation Requirements
Please ensure the generated code meets these requirements:

{{range .Validations}}{{range .Validations}}- {{.Name}} ({{.Type}}): {{.Description}}{{if .Details}}
  Details: {{.Details}}{{end}}{{end}}{{end}}
{{end}}

## Instructions
1. Generate the code to implement all the features described above
2. Create all necessary files and directories  
3. Follow best practices for the programming language and framework
4. Ensure the code meets all validation constraints
5. Include appropriate error handling and logging
6. Write clean, maintainable code

Please generate the complete implementation.`,

	Refine: `# Refinement Request

Target: {{.TargetName}}
User feedback: {{.UserFeedback}}

{{if .GeneratedFiles}}
## Previously Generated Files
{{range .GeneratedFiles}}- {{.}}
{{end}}
{{end}}

{{if .ValidationErrors}}
## Validation Errors to Fix
{{range .ValidationErrors}}- {{.}}
{{end}}
{{end}}

Please refine the implementation based on the feedback{{if .ValidationErrors}} and fix the validation errors{{end}}.`,

	Validate: `# Validation Request

Validation: {{.ValidationName}}
Type: {{.ValidationType}}
Description: {{.ValidationDescription}}
{{if .ValidationDetails}}Details: {{.ValidationDetails}}{{end}}

## Generated Files
{{range .GeneratedFiles}}- {{.}}
{{end}}

Please verify if the generated code meets this validation constraint. 
Respond with 'PASS' or 'FAIL' followed by an explanation.`,
}

// PromptData contains the data used to fill in prompt templates
type PromptData struct {
	// Common fields
	ProjectRoot      string
	GenerationID     string
	IntentName       string
	IntentContent    string
	Dependencies     string
	Validations      []*src.ValidationFile
	GeneratedFiles   []string
	WorkingDir       string
	
	// Refinement fields
	TargetName       string
	UserFeedback     string
	ValidationErrors []string
	
	// Validation fields
	ValidationName        string
	ValidationType        string
	ValidationDescription string
	ValidationDetails     string
}

// ValidationData is used for template rendering
type ValidationData struct {
	Name        string
	Type        string
	Description string
	Details     string
}

// GetValidationDetails extracts the Details parameter from a validation
func GetValidationDetails(v *src.Validation) string {
	if details, ok := v.Parameters["Details"].(string); ok {
		return details
	}
	return ""
}