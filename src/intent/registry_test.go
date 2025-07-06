package intent

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestTargetRegistry_LoadTargets(t *testing.T) {
	tmpDir := t.TempDir()
	
	// Create intent directory structure
	intentDir := filepath.Join(tmpDir, "intent")
	os.MkdirAll(filepath.Join(intentDir, "auth"), 0755)
	os.MkdirAll(filepath.Join(intentDir, "user"), 0755)
	
	// Create intent files
	authIntent := `# Feature: Authentication
## Dependencies
- user

Authentication system`
	userIntent := `# Feature: User Management

User management system`
	
	os.WriteFile(filepath.Join(intentDir, "auth", "auth.ic"), []byte(authIntent), 0644)
	os.WriteFile(filepath.Join(intentDir, "user", "user.ic"), []byte(userIntent), 0644)
	
	// Create validation files
	os.WriteFile(filepath.Join(intentDir, "auth", "auth.icv"), []byte("validations"), 0644)
	
	registry := NewTargetRegistry(tmpDir)
	err := registry.LoadTargets()
	if err != nil {
		t.Fatalf("LoadTargets failed: %v", err)
	}
	
	// Check targets were loaded
	targets := registry.GetAllTargets()
	if len(targets) != 2 {
		t.Errorf("Expected 2 targets, got %d", len(targets))
	}
	
	// Check specific target
	authTarget, exists := registry.GetTarget("Authentication")
	if !exists {
		t.Error("Authentication target not found")
	} else {
		if len(authTarget.Intent.Dependencies) == 0 {
			t.Error("Expected at least 1 dependency")
		}
		if len(authTarget.ValidationFiles) != 1 {
			t.Errorf("Expected 1 validation file, got %d", len(authTarget.ValidationFiles))
		}
	}
	
	// Check aliases
	allTargets, exists := registry.GetAlias("all")
	if !exists || len(allTargets) != 2 {
		t.Error("'all' alias not properly set")
	}
}

func TestTargetRegistry_Caching(t *testing.T) {
	tmpDir := t.TempDir()
	registry := NewTargetRegistry(tmpDir)
	
	// Create a test intent file
	intentPath := filepath.Join(tmpDir, "test.ic")
	intentContent := `# Feature: Test
Test content`
	os.WriteFile(intentPath, []byte(intentContent), 0644)
	
	// Parse and cache
	parser := NewParser()
	intent1, err := parser.ParseIntentFile(intentPath)
	if err != nil {
		t.Fatalf("Failed to parse intent: %v", err)
	}
	
	registry.CacheIntent(intentPath, intent1)
	
	// Retrieve from cache
	cached, found := registry.GetCachedIntent(intentPath)
	if !found {
		t.Error("Intent not found in cache")
	}
	if cached.Name != intent1.Name {
		t.Error("Cached intent doesn't match original")
	}
	
	// Modify file
	time.Sleep(10 * time.Millisecond)
	os.WriteFile(intentPath, []byte("Modified content"), 0644)
	
	// Cache should be invalidated
	_, found = registry.GetCachedIntent(intentPath)
	if found {
		t.Error("Cache should be invalidated after file modification")
	}
}

func TestTargetRegistry_RefreshTarget(t *testing.T) {
	tmpDir := t.TempDir()
	registry := NewTargetRegistry(tmpDir)
	
	// Create and register a target
	intentPath := filepath.Join(tmpDir, "test.ic")
	initialContent := `# Feature: Test
Initial content`
	os.WriteFile(intentPath, []byte(initialContent), 0644)
	
	intent := &IntentFile{
		Name: "Test",
		Path: intentPath,
		Description: "Initial",
	}
	registry.RegisterTarget(intent)
	
	// Modify the file
	time.Sleep(10 * time.Millisecond)
	updatedContent := `# Feature: Test Updated
## Description
Updated content`
	os.WriteFile(intentPath, []byte(updatedContent), 0644)
	
	// Refresh the target
	err := registry.RefreshTarget("Test")
	if err != nil {
		t.Fatalf("RefreshTarget failed: %v", err)
	}
	
	// Check that target was updated
	target, exists := registry.GetTarget("Test")
	if !exists {
		t.Fatal("Target not found after refresh")
	}
	
	if target.Intent.Name != "Test Updated" {
		t.Errorf("Target name not updated, got %s", target.Intent.Name)
	}
}

func TestTargetRegistry_InvalidateCache(t *testing.T) {
	tmpDir := t.TempDir()
	registry := NewTargetRegistry(tmpDir)
	
	// Create actual files for caching to work
	path1 := filepath.Join(tmpDir, "test1.ic")
	path2 := filepath.Join(tmpDir, "test2.ic")
	os.WriteFile(path1, []byte("content1"), 0644)
	os.WriteFile(path2, []byte("content2"), 0644)
	
	// Cache some intents
	intent1 := &IntentFile{Name: "Test1", Path: path1}
	intent2 := &IntentFile{Name: "Test2", Path: path2}
	
	registry.CacheIntent(path1, intent1)
	registry.CacheIntent(path2, intent2)
	
	// Verify cached
	_, found1 := registry.GetCachedIntent(path1)
	_, found2 := registry.GetCachedIntent(path2)
	if !found1 || !found2 {
		t.Error("Intents should be cached")
	}
	
	// Invalidate one
	registry.InvalidateCache(path1)
	
	_, found1 = registry.GetCachedIntent(path1)
	_, found2 = registry.GetCachedIntent(path2)
	
	if found1 {
		t.Error("test1.ic should be invalidated")
	}
	if !found2 {
		t.Error("test2.ic should still be cached")
	}
	
	// Clear all cache
	registry.ClearCache()
	
	_, found2 = registry.GetCachedIntent(path2)
	if found2 {
		t.Error("Cache should be cleared")
	}
}

func TestTargetRegistry_DefaultAliases(t *testing.T) {
	tmpDir := t.TempDir()
	
	// Create mixed intent types
	intentDir := filepath.Join(tmpDir, "intent")
	os.MkdirAll(filepath.Join(intentDir, "project"), 0755)
	os.MkdirAll(filepath.Join(intentDir, "auth"), 0755)
	os.MkdirAll(filepath.Join(intentDir, "user"), 0755)
	
	projectIntent := `# Project: Main App
Main application`
	featureIntent1 := `# Feature: Auth
Authentication`
	featureIntent2 := `# Feature: User
User management`
	
	os.WriteFile(filepath.Join(intentDir, "project", "main.ic"), []byte(projectIntent), 0644)
	os.WriteFile(filepath.Join(intentDir, "auth", "auth.ic"), []byte(featureIntent1), 0644)
	os.WriteFile(filepath.Join(intentDir, "user", "user.ic"), []byte(featureIntent2), 0644)
	
	registry := NewTargetRegistry(tmpDir)
	err := registry.LoadTargets()
	if err != nil {
		t.Fatalf("LoadTargets failed: %v", err)
	}
	
	// Check project alias
	projectTargets, exists := registry.GetAlias("project")
	if !exists {
		t.Error("'project' alias not found")
	} else if len(projectTargets) != 1 {
		t.Errorf("Expected 1 project target, got %d", len(projectTargets))
	}
	
	// Check features alias
	featureTargets, exists := registry.GetAlias("features")
	if !exists {
		t.Error("'features' alias not found")
	} else if len(featureTargets) != 2 {
		t.Errorf("Expected 2 feature targets, got %d", len(featureTargets))
	}
	
	// Check all alias
	allTargets, exists := registry.GetAlias("all")
	if !exists {
		t.Error("'all' alias not found")
	} else if len(allTargets) != 3 {
		t.Errorf("Expected 3 total targets, got %d", len(allTargets))
	}
}