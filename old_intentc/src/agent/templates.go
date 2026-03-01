package agent

import "github.com/pboueri/intentc/src"

// PromptTemplates contains all the prompt templates used by agents
type PromptTemplates struct {
	Build     string
	Refine    string
	Validate  string
	Decompile string
}

// DefaultPromptTemplates returns the default prompt templates
var DefaultPromptTemplates = PromptTemplates{
	Build: `# Code Generation Request

Project: {{.ProjectRoot}}
Build Directory: {{.BuildPath}}
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
2. Create all necessary files and directories in the build directory: {{.BuildPath}}
3. Your current working directory is set to the build directory
4. Follow best practices for the programming language and framework
5. Ensure the code meets all validation constraints
6. Include appropriate error handling and logging
7. Write clean, maintainable code

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

	Decompile: `DECOMPILE TASK: Analyze the codebase and generate intent files

Source codebase location: {{.SourcePath}}
Output location: {{.OutputPath}}

Your task is to:
1. Explore and analyze the codebase at: {{.SourcePath}}
2. Understand its purpose and structure
3. Generate intent files at: {{.OutputPath}} based on your analysis

CRITICAL INSTRUCTIONS FOR ABSTRACT THINKING:
1. DO NOT describe implementation details (e.g., 'uses Express.js', 'implements JWT')
2. DO describe capabilities and value (e.g., 'provides secure access control', 'enables web-based interactions')
3. Think like a product manager, not a developer
4. Focus on user/business value, not technical architecture

STEP-BY-STEP PROCESS:
1. First, explore the codebase structure at {{.SourcePath}} to understand its organization
2. Read key files (README, package.json, main entry points) in that directory
3. Identify the major feature areas by examining the source code
4. For each feature area, determine:
   - What capability it provides to users/business
   - What problems it solves
   - How it relates to other features
5. Create intent files at {{.OutputPath}} that capture these abstract concepts

FILES TO CREATE:

1. project.ic - Main project intent file:
` + "```" + `markdown
# Project: [Descriptive Project Name]

## Overview
[2-3 sentences describing what value this project delivers]

## Core Capabilities
- [Capability 1]: [Brief description]
- [Capability 2]: [Brief description]
- ...

## Features
- feature-[name]: [One-line description]
- feature-[name]: [One-line description]
- ...
` + "```" + `

2. feature-[name].ic files for each major capability:
` + "```" + `markdown
# Feature: [Human-Friendly Feature Name]

## Overview
[What user/business need does this feature address?]

## User Stories
- As a [user type], I want to [action] so that [benefit]
- ...

## Targets

### Target: core-[functionality]
Type: implementation
Description: [What this component should enable]
Dependencies: []

### Target: interface-[type]
Type: implementation
Description: [How users interact with this capability]
Dependencies: [core-functionality]
` + "```" + `

3. validation-[feature].icv files:
` + "```" + `markdown
# Validations: [Feature Name]

## Validation: [scenario-name]
Type: [functional/integration/acceptance]
Description: Verify that [expected behavior]
Target: [target-name]

### Success Criteria
- [Measurable criterion 1]
- [Measurable criterion 2]
` + "```" + `

EXAMPLES OF ABSTRACT VS CONCRETE THINKING:

❌ WRONG (Too Concrete):
- 'REST API with Express.js'
- 'PostgreSQL database schema'
- 'React components with Redux'

✅ RIGHT (Properly Abstract):
- 'External integration interface'
- 'Persistent information storage'
- 'Interactive user experience'

❌ WRONG Target:
Target: express-server
Description: Set up Express.js server with middleware

✅ RIGHT Target:
Target: service-foundation
Description: Enable the system to receive and respond to external requests

Remember: The goal is to create intent files that could be given to a completely different team
using different technologies, and they would still build something that achieves the same
business/user value. Technology choices are implementation details, not intents.

USING INTENTC FOR VALIDATION:
After creating the intent files, you can use intentc commands to validate your work:
- 'intentc check' - Validates that the intent files are properly formatted
- 'intentc status' - Shows the status of all targets
- 'intentc validate' - Runs validation checks

IMPORTANT: DO NOT use 'intentc build' - we are decompiling, not building!

Now:
1. Analyze the codebase at: {{.SourcePath}}
2. Create the appropriate intent files at: {{.OutputPath}}
3. Use 'intentc check' to validate the files are properly formatted
4. Use 'intentc status' to verify all targets are recognized

IMPORTANT: After creating each file, output a line like 'Created: filename.ic' so the system knows which files were generated.`,
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
	
	// Decompile fields
	SourcePath string
	OutputPath string
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