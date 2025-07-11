package agent

import (
	"bytes"
	"testing"
	"text/template"
	"time"

	"github.com/pboueri/intentc/src"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestNewClaudeAgent(t *testing.T) {
	tests := []struct {
		name   string
		config ClaudeAgentConfig
		check  func(t *testing.T, agent *ClaudeAgent)
	}{
		{
			name:   "default config",
			config: ClaudeAgentConfig{},
			check: func(t *testing.T, agent *ClaudeAgent) {
				assert.Equal(t, "test-agent", agent.GetName())
				assert.Equal(t, "claude", agent.GetType())
				assert.Equal(t, "claude", agent.command)
				assert.Equal(t, 5*time.Minute, agent.timeout)
				assert.Equal(t, 3, agent.retries)
				assert.Equal(t, time.Second, agent.rateLimit)
			},
		},
		{
			name: "custom config",
			config: ClaudeAgentConfig{
				Timeout:   10 * time.Minute,
				Retries:   5,
				RateLimit: 2 * time.Second,
				CLIArgs:   []string{"--verbose"},
			},
			check: func(t *testing.T, agent *ClaudeAgent) {
				assert.Equal(t, "test-agent", agent.GetName())
				assert.Equal(t, "claude", agent.GetType())
				assert.Equal(t, 10*time.Minute, agent.timeout)
				assert.Equal(t, 5, agent.retries)
				assert.Equal(t, 2*time.Second, agent.rateLimit)
				assert.Equal(t, []string{"--verbose"}, agent.args)
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			agent := NewClaudeAgent("test-agent", tt.config)
			require.NotNil(t, agent)
			tt.check(t, agent)
		})
	}
}

func TestClaudeBuildPrompt(t *testing.T) {
	agent := NewClaudeAgent("test", ClaudeAgentConfig{})
	
	buildCtx := BuildContext{
		Intent: &src.Intent{
			Name:         "test-feature",
			Dependencies: []string{"dep1", "dep2"},
			Content:      "Build a test feature",
		},
		Validations: []*src.ValidationFile{
			{
				Validations: []src.Validation{
					{
						Name:        "file_exists",
						Type:        src.ValidationTypeFileCheck,
						Description: "Check main.go exists",
						Parameters: map[string]interface{}{
							"Details": "Must have main function",
						},
					},
				},
			},
		},
		ProjectRoot:  "/test/project",
		GenerationID: "gen-123",
	}

	prompt, err := agent.createClaudeBuildPrompt(buildCtx)
	require.NoError(t, err)

	// Check that prompt contains expected content
	assert.Contains(t, prompt, "Code Generation Request")
	assert.Contains(t, prompt, "/test/project")
	assert.Contains(t, prompt, "gen-123")
	assert.Contains(t, prompt, "test-feature")
	assert.Contains(t, prompt, "dep1, dep2")
	assert.Contains(t, prompt, "Build a test feature")
	assert.Contains(t, prompt, "file_exists")
	assert.Contains(t, prompt, "Check main.go exists")
	assert.Contains(t, prompt, "Must have main function")
}

func TestClaudeValidatePrompt(t *testing.T) {
	agent := NewClaudeAgent("test", ClaudeAgentConfig{})
	
	// Create a simple validation
	validation := &src.Validation{
		Name:        "test_validation",
		Type:        src.ValidationTypeFileCheck,
		Description: "Test validation description",
		Parameters: map[string]interface{}{
			"Details": "Additional details",
		},
	}
	
	// Test template generation directly
	tmpl, err := template.New("validate").Parse(agent.templates.Validate)
	require.NoError(t, err)
	
	data := PromptData{
		ValidationName:        validation.Name,
		ValidationType:        string(validation.Type),
		ValidationDescription: validation.Description,
		ValidationDetails:     GetValidationDetails(validation),
		GeneratedFiles:        []string{"file1.go", "file2.go"},
	}
	
	var buf bytes.Buffer
	err = tmpl.Execute(&buf, data)
	require.NoError(t, err)
	
	prompt := buf.String()
	
	// Check the prompt content
	assert.Contains(t, prompt, "test_validation")
	assert.Contains(t, prompt, "FileCheck")
	assert.Contains(t, prompt, "Test validation description")
	assert.Contains(t, prompt, "Additional details")
	assert.Contains(t, prompt, "file1.go")
	assert.Contains(t, prompt, "file2.go")
}

func TestClaudeRefinePrompt(t *testing.T) {
	agent := NewClaudeAgent("test", ClaudeAgentConfig{})
	
	// Test template generation directly
	tmpl, err := template.New("refine").Parse(agent.templates.Refine)
	require.NoError(t, err)
	
	data := PromptData{
		TargetName:   "test-target",
		UserFeedback: "Please add error handling",
	}
	
	var buf bytes.Buffer
	err = tmpl.Execute(&buf, data)
	require.NoError(t, err)
	
	prompt := buf.String()
	
	// Check the prompt content
	assert.Contains(t, prompt, "Refinement Request")
	assert.Contains(t, prompt, "test-target")
	assert.Contains(t, prompt, "Please add error handling")
}

func TestClaudeParseGeneratedFiles(t *testing.T) {
	agent := NewClaudeAgent("test", ClaudeAgentConfig{})
	
	// Set the working directory for the agent
	agent.workingDir = "/test/project"
	
	buildCtx := BuildContext{
		Intent: &src.Intent{
			Name: "test-feature",
		},
		ProjectRoot: "/test/project",
		BuildPath:   "/test/project",
	}

	tests := []struct {
		name     string
		output   string
		expected []string
	}{
		{
			name: "Claude specific patterns",
			output: `Creating implementation...
Created file: src/main.go
Generated: src/utils.go
Writing to: config/app.yaml
Done.`,
			expected: []string{
				"/test/project/src/main.go",
				"/test/project/src/utils.go",
				"/test/project/config/app.yaml",
			},
		},
		{
			name: "No files parsed",
			output: `Just some output without file indicators`,
			expected: []string{},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			files := agent.parseClaudeGeneratedFiles(tt.output, buildCtx)
			assert.Equal(t, tt.expected, files)
		})
	}
}

func TestClaudeTemplateCustomization(t *testing.T) {
	agent := NewClaudeAgent("test", ClaudeAgentConfig{})
	
	// Create custom templates
	customTemplates := PromptTemplates{
		Build:    "CUSTOM BUILD: {{.IntentName}}",
		Refine:   "CUSTOM REFINE: {{.TargetName}}",
		Validate: "CUSTOM VALIDATE: {{.ValidationName}}",
	}
	
	agent.SetTemplates(customTemplates)
	
	// Test that custom templates are used
	buildCtx := BuildContext{
		Intent: &src.Intent{
			Name:    "test-intent",
			Content: "test content",
		},
		ProjectRoot:  "/test",
		GenerationID: "123",
	}
	
	prompt, err := agent.createClaudeBuildPrompt(buildCtx)
	require.NoError(t, err)
	assert.Equal(t, "CUSTOM BUILD: test-intent", prompt)
}

func TestClaudeAgentFactory(t *testing.T) {
	factory := NewClaudeAgentFactory(ClaudeAgentConfig{
		Timeout: 3 * time.Minute,
		Retries: 2,
	})

	agentConfig := src.Agent{
		Name: "test-claude",
		Type: "claude",
		Config: map[string]interface{}{
			"timeout":   "10m",
			"retries":   float64(5),
			"rate_limit": "3s",
			"cli_args": []interface{}{"--verbose", "--json"},
		},
	}

	agent, err := factory.CreateAgent(agentConfig)
	require.NoError(t, err)
	require.NotNil(t, agent)

	claudeAgent, ok := agent.(*ClaudeAgent)
	require.True(t, ok)

	assert.Equal(t, "test-claude", claudeAgent.GetName())
	assert.Equal(t, "claude", claudeAgent.GetType())
	assert.Equal(t, 10*time.Minute, claudeAgent.timeout)
	assert.Equal(t, 5, claudeAgent.retries)
	assert.Equal(t, 3*time.Second, claudeAgent.rateLimit)
	assert.Equal(t, []string{"--verbose", "--json"}, claudeAgent.args)
}

func TestClaudePromptTemplateStructure(t *testing.T) {
	// Test that the default templates are properly structured
	templates := DefaultPromptTemplates
	
	// Build template should contain key sections
	assert.Contains(t, templates.Build, "Code Generation Request")
	assert.Contains(t, templates.Build, "Project:")
	assert.Contains(t, templates.Build, "Intent")
	assert.Contains(t, templates.Build, "Instructions")
	
	// Refine template should contain key sections
	assert.Contains(t, templates.Refine, "Refinement Request")
	assert.Contains(t, templates.Refine, "Target:")
	assert.Contains(t, templates.Refine, "User feedback:")
	
	// Validate template should contain key sections
	assert.Contains(t, templates.Validate, "Validation Request")
	assert.Contains(t, templates.Validate, "Generated Files")
	assert.Contains(t, templates.Validate, "PASS")
	assert.Contains(t, templates.Validate, "FAIL")
}