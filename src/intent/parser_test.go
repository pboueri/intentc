package intent

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParser_ParseIntentFile(t *testing.T) {
	parser := NewParser()

	tests := []struct {
		name        string
		content     string
		wantType    IntentType
		wantName    string
		wantDeps    []string
		wantTargets int
		wantErr     bool
	}{
		{
			name: "feature intent with all sections",
			content: `# Feature: Authentication System

## Dependencies
- user-system
- database

## Description
This feature provides secure user authentication with JWT tokens.

## User Experience
Users can log in with email and password, and receive a token for API access.

## Quality Goals
- Login response time < 200ms
- Support 10k concurrent users`,
			wantType:    IntentTypeFeature,
			wantName:    "Authentication System",
			wantDeps:    []string{"user-system", "database"},
			wantTargets: 0,
		},
		{
			name: "project intent without dependencies",
			content: `# Project: My App

## Description
A web application for task management.

## User Experience
Simple and intuitive interface for managing daily tasks.`,
			wantType:    IntentTypeProject,
			wantName:    "My App",
			wantDeps:    []string{},
			wantTargets: 0,
		},
		{
			name: "intent with targets",
			content: `# Feature: API Gateway

## Target: rest-api

RESTful API implementation with authentication.

## Target: graphql-api

GraphQL API with schema and resolvers.`,
			wantType:    IntentTypeFeature,
			wantName:    "API Gateway",
			wantDeps:    []string{},
			wantTargets: 2,
		},
		{
			name: "intent with inline dependencies",
			content: `# Feature: Reports
Depends On: auth, database, export-service

Generate various reports for users.`,
			wantType: IntentTypeFeature,
			wantName: "Reports",
			wantDeps: []string{"auth", "database", "export-service"},
		},
		{
			name: "intent with code blocks",
			content: "# Feature: Code Parser\n\n## Description\nParse code with examples:\n\n```go\nfunc main() {\n    fmt.Println(\"Hello\")\n}\n```\n\n## Dependencies\n- ast-parser",
			wantType: IntentTypeFeature,
			wantName: "Code Parser",
			wantDeps: []string{"ast-parser"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Create temp file
			tmpDir := t.TempDir()
			filePath := filepath.Join(tmpDir, "test.ic")
			if err := os.WriteFile(filePath, []byte(tt.content), 0644); err != nil {
				t.Fatalf("Failed to write test file: %v", err)
			}

			intent, err := parser.ParseIntentFile(filePath)
			if (err != nil) != tt.wantErr {
				t.Errorf("ParseIntentFile() error = %v, wantErr %v", err, tt.wantErr)
				return
			}

			if err == nil {
				if intent.Type != tt.wantType {
					t.Errorf("Type = %v, want %v", intent.Type, tt.wantType)
				}
				if intent.Name != tt.wantName {
					t.Errorf("Name = %v, want %v", intent.Name, tt.wantName)
				}
				if len(intent.Dependencies) != len(tt.wantDeps) {
					t.Errorf("Dependencies = %v, want %v", intent.Dependencies, tt.wantDeps)
				} else {
					for i, dep := range intent.Dependencies {
						if dep != tt.wantDeps[i] {
							t.Errorf("Dependency[%d] = %v, want %v", i, dep, tt.wantDeps[i])
						}
					}
				}
				if len(intent.Targets) != tt.wantTargets {
					t.Errorf("Targets = %d, want %d", len(intent.Targets), tt.wantTargets)
				}
			}
		})
	}
}

func TestParser_DiscoverIntentFiles(t *testing.T) {
	parser := NewParser()

	// Create test directory structure
	tmpDir := t.TempDir()
	
	// Standard structure
	intentDir := filepath.Join(tmpDir, "intent")
	os.MkdirAll(filepath.Join(intentDir, "auth"), 0755)
	os.MkdirAll(filepath.Join(intentDir, "user"), 0755)
	
	// New structure
	os.MkdirAll(filepath.Join(intentDir, "project"), 0755)
	os.MkdirAll(filepath.Join(intentDir, "features", "payment"), 0755)
	
	// Create intent files
	files := map[string]string{
		filepath.Join(intentDir, "auth", "auth.ic"):                    "# Feature: Auth\n\nAuthentication system",
		filepath.Join(intentDir, "user", "user.ic"):                    "# Feature: User\n\nUser management",
		filepath.Join(intentDir, "project", "main.ic"):                 "# Project: Main\n\nMain project intent",
		filepath.Join(intentDir, "features", "payment", "payment.ic"):  "# Feature: Payment\n\nPayment processing",
	}
	
	for path, content := range files {
		if err := os.WriteFile(path, []byte(content), 0644); err != nil {
			t.Fatalf("Failed to create test file %s: %v", path, err)
		}
	}
	
	// Also create some non-intent files
	os.WriteFile(filepath.Join(intentDir, "auth", "auth.icv"), []byte("validation"), 0644)
	os.WriteFile(filepath.Join(intentDir, "README.md"), []byte("readme"), 0644)
	
	intents, err := parser.DiscoverIntentFiles(tmpDir)
	if err != nil {
		t.Fatalf("DiscoverIntentFiles failed: %v", err)
	}
	
	if len(intents) != 4 {
		t.Errorf("Expected 4 intents, got %d", len(intents))
	}
	
	// Check that all expected intents were found
	foundNames := make(map[string]bool)
	for _, intent := range intents {
		foundNames[intent.Name] = true
	}
	
	expectedNames := []string{"auth", "user", "project", "payment"}
	for _, name := range expectedNames {
		if !foundNames[name] {
			t.Errorf("Expected to find intent %s", name)
		}
	}
}

func TestParser_ParseDependencies(t *testing.T) {
	parser := NewParser()

	tests := []struct {
		name    string
		content string
		want    []string
	}{
		{
			name: "bullet list",
			content: `- auth
- database
- cache`,
			want: []string{"auth", "database", "cache"},
		},
		{
			name: "comma separated",
			content: `auth, database, cache`,
			want: []string{"auth", "database", "cache"},
		},
		{
			name: "mixed format",
			content: `- auth
database, cache
- api`,
			want: []string{"auth", "database", "cache", "api"},
		},
		{
			name:    "empty",
			content: ``,
			want:    []string{},
		},
		{
			name: "with comments",
			content: `- auth
# This is a comment
- database`,
			want: []string{"auth", "database"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := parser.parseDependencies(tt.content)
			if len(got) != len(tt.want) {
				t.Errorf("parseDependencies() = %v, want %v", got, tt.want)
				return
			}
			for i, dep := range got {
				if dep != tt.want[i] {
					t.Errorf("dependency[%d] = %v, want %v", i, dep, tt.want[i])
				}
			}
		})
	}
}

func TestParser_ConvertToLegacyIntent(t *testing.T) {
	parser := NewParser()

	intentFile := &IntentFile{
		Path:         "/test/auth.ic",
		Type:         IntentTypeFeature,
		Name:         "Authentication",
		Dependencies: []string{"user", "database"},
		Description:  "Auth system description",
		UserExperience: "Users can log in",
		QualityGoals: "Fast and secure",
		Targets: []Target{
			{Name: "api", Content: "REST API"},
		},
	}

	legacy := parser.ConvertToLegacyIntent(intentFile)

	if legacy.Name != "Authentication" {
		t.Errorf("Name = %v, want Authentication", legacy.Name)
	}

	if len(legacy.Dependencies) != 2 {
		t.Errorf("Dependencies = %v, want 2", len(legacy.Dependencies))
	}

	if !strings.Contains(legacy.Content, "## Description") {
		t.Error("Content should contain Description section")
	}

	if !strings.Contains(legacy.Content, "## User Experience") {
		t.Error("Content should contain User Experience section")
	}

	if !strings.Contains(legacy.Content, "## Target: api") {
		t.Error("Content should contain Target section")
	}
}