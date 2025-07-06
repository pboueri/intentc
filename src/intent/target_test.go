package intent

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func createTestRegistry(t *testing.T) (*TargetRegistry, string) {
	tmpDir := t.TempDir()
	registry := NewTargetRegistry(tmpDir)

	// Create some test targets
	intents := []*IntentFile{
		{Name: "auth-system", Path: filepath.Join(tmpDir, "auth.ic")},
		{Name: "user-service", Path: filepath.Join(tmpDir, "user.ic")},
		{Name: "api-gateway", Path: filepath.Join(tmpDir, "api.ic")},
		{Name: "auth-api", Path: filepath.Join(tmpDir, "auth-api.ic")},
		{Name: "test-utils", Path: filepath.Join(tmpDir, "test.ic")},
	}

	for _, intent := range intents {
		// Create the file
		os.WriteFile(intent.Path, []byte("# "+intent.Name), 0644)
		registry.RegisterTarget(intent)
	}

	return registry, tmpDir
}

func TestTargetResolver_ResolveExactMatch(t *testing.T) {
	registry, _ := createTestRegistry(t)
	resolver := NewTargetResolver(registry)

	targets, err := resolver.ResolveTargets([]string{"auth-system"})
	if err != nil {
		t.Fatalf("ResolveTargets failed: %v", err)
	}

	if len(targets) != 1 {
		t.Errorf("Expected 1 target, got %d", len(targets))
	}

	if targets[0].Name != "auth-system" {
		t.Errorf("Expected auth-system, got %s", targets[0].Name)
	}
}

func TestTargetResolver_ResolveWildcard(t *testing.T) {
	registry, _ := createTestRegistry(t)
	resolver := NewTargetResolver(registry)

	tests := []struct {
		name     string
		pattern  string
		expected []string
	}{
		{
			name:     "prefix wildcard",
			pattern:  "auth-*",
			expected: []string{"auth-system", "auth-api"},
		},
		{
			name:     "suffix wildcard",
			pattern:  "*-service",
			expected: []string{"user-service"},
		},
		{
			name:     "middle wildcard",
			pattern:  "*api*",
			expected: []string{"api-gateway", "auth-api"},
		},
		{
			name:     "all wildcard",
			pattern:  "*",
			expected: []string{"auth-system", "user-service", "api-gateway", "auth-api", "test-utils"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			targets, err := resolver.ResolveTargets([]string{tt.pattern})
			if err != nil {
				t.Fatalf("ResolveTargets failed: %v", err)
			}

			if len(targets) != len(tt.expected) {
				t.Errorf("Expected %d targets, got %d", len(tt.expected), len(targets))
				for _, target := range targets {
					t.Logf("Found: %s", target.Name)
				}
				return
			}

			// Check that all expected targets are found
			found := make(map[string]bool)
			for _, target := range targets {
				found[target.Name] = true
			}

			for _, expected := range tt.expected {
				if !found[expected] {
					t.Errorf("Expected to find target %s", expected)
				}
			}
		})
	}
}

func TestTargetResolver_ResolveMultiplePatterns(t *testing.T) {
	registry, _ := createTestRegistry(t)
	resolver := NewTargetResolver(registry)

	targets, err := resolver.ResolveTargets([]string{"auth-*", "user-service"})
	if err != nil {
		t.Fatalf("ResolveTargets failed: %v", err)
	}

	// Should get auth-system, auth-api, and user-service (no duplicates)
	if len(targets) != 3 {
		t.Errorf("Expected 3 targets, got %d", len(targets))
	}
}

func TestTargetResolver_ResolveNoMatch(t *testing.T) {
	registry, _ := createTestRegistry(t)
	resolver := NewTargetResolver(registry)

	_, err := resolver.ResolveTargets([]string{"nonexistent"})
	if err == nil {
		t.Error("Expected error for non-matching pattern")
	}
}

func TestTargetResolver_ResolveEmpty(t *testing.T) {
	registry, _ := createTestRegistry(t)
	resolver := NewTargetResolver(registry)

	targets, err := resolver.ResolveTargets([]string{})
	if err != nil {
		t.Fatalf("ResolveTargets failed: %v", err)
	}

	// Should return all targets
	if len(targets) != 5 {
		t.Errorf("Expected 5 targets, got %d", len(targets))
	}
}

func TestTargetResolver_CheckIfUpToDate(t *testing.T) {
	registry, tmpDir := createTestRegistry(t)
	resolver := NewTargetResolver(registry)

	// Get a target
	target, _ := registry.GetTarget("auth-system")

	// Create generated files
	genFile1 := filepath.Join(tmpDir, "gen", "auth1.go")
	genFile2 := filepath.Join(tmpDir, "gen", "auth2.go")
	os.MkdirAll(filepath.Dir(genFile1), 0755)

	// Test 1: No generated files exist - should not be up to date
	upToDate, err := resolver.CheckIfUpToDate(target, []string{genFile1, genFile2})
	if err != nil {
		t.Fatalf("CheckIfUpToDate failed: %v", err)
	}
	if upToDate {
		t.Error("Target should not be up to date when generated files don't exist")
	}

	// Create generated files
	time.Sleep(10 * time.Millisecond) // Ensure different timestamps
	os.WriteFile(genFile1, []byte("generated"), 0644)
	os.WriteFile(genFile2, []byte("generated"), 0644)

	// Test 2: Generated files are newer - should be up to date
	upToDate, err = resolver.CheckIfUpToDate(target, []string{genFile1, genFile2})
	if err != nil {
		t.Fatalf("CheckIfUpToDate failed: %v", err)
	}
	if !upToDate {
		t.Error("Target should be up to date when generated files are newer")
	}

	// Test 3: Touch intent file to make it newer
	time.Sleep(10 * time.Millisecond)
	os.Chtimes(target.Intent.Path, time.Now(), time.Now())

	upToDate, err = resolver.CheckIfUpToDate(target, []string{genFile1, genFile2})
	if err != nil {
		t.Fatalf("CheckIfUpToDate failed: %v", err)
	}
	if upToDate {
		t.Error("Target should not be up to date when intent is newer than generated files")
	}
}

func TestTargetResolver_ExpandAliases(t *testing.T) {
	registry, _ := createTestRegistry(t)
	resolver := NewTargetResolver(registry)

	// Add some aliases
	registry.AddAlias("auth", []string{"auth-system", "auth-api"})
	registry.AddAlias("core", []string{"user-service", "api-gateway"})

	tests := []struct {
		name     string
		input    []string
		expected []string
	}{
		{
			name:     "single alias",
			input:    []string{"auth"},
			expected: []string{"auth-system", "auth-api"},
		},
		{
			name:     "multiple aliases",
			input:    []string{"auth", "core"},
			expected: []string{"auth-system", "auth-api", "user-service", "api-gateway"},
		},
		{
			name:     "mix of alias and regular",
			input:    []string{"auth", "test-utils"},
			expected: []string{"auth-system", "auth-api", "test-utils"},
		},
		{
			name:     "non-existent alias",
			input:    []string{"nonexistent"},
			expected: []string{"nonexistent"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			expanded := resolver.ExpandAliases(tt.input)
			
			if len(expanded) != len(tt.expected) {
				t.Errorf("Expected %d targets, got %d", len(tt.expected), len(expanded))
				return
			}

			// Check all expected values are present
			found := make(map[string]bool)
			for _, name := range expanded {
				found[name] = true
			}

			for _, expected := range tt.expected {
				if !found[expected] {
					t.Errorf("Expected to find %s in expanded list", expected)
				}
			}
		})
	}
}