package cleaner

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/pboueri/intentc/src"
)

type mockStateManager struct {
	statuses     map[string]src.TargetStatus
	buildResults map[string]*src.BuildResult
}

func newMockStateManager() *mockStateManager {
	return &mockStateManager{
		statuses:     make(map[string]src.TargetStatus),
		buildResults: make(map[string]*src.BuildResult),
	}
}

func (m *mockStateManager) Initialize(ctx context.Context) error {
	return nil
}

func (m *mockStateManager) SaveBuildResult(ctx context.Context, result *src.BuildResult) error {
	m.buildResults[result.Target] = result
	return nil
}

func (m *mockStateManager) GetBuildResult(ctx context.Context, target string, generationID string) (*src.BuildResult, error) {
	return m.buildResults[target], nil
}

func (m *mockStateManager) GetLatestBuildResult(ctx context.Context, target string) (*src.BuildResult, error) {
	return m.buildResults[target], nil
}

func (m *mockStateManager) ListBuildResults(ctx context.Context, target string) ([]*src.BuildResult, error) {
	if result, ok := m.buildResults[target]; ok {
		return []*src.BuildResult{result}, nil
	}
	return []*src.BuildResult{}, nil
}

func (m *mockStateManager) CommitChanges(ctx context.Context, message string, files []string) error {
	return nil
}

func (m *mockStateManager) GetTargetStatus(ctx context.Context, target string) (src.TargetStatus, error) {
	status, ok := m.statuses[target]
	if !ok {
		return src.TargetStatusPending, nil
	}
	return status, nil
}

func (m *mockStateManager) UpdateTargetStatus(ctx context.Context, target string, status src.TargetStatus) error {
	m.statuses[target] = status
	return nil
}

func setupTestProject(t *testing.T) string {
	tmpDir := t.TempDir()
	intentDir := filepath.Join(tmpDir, "intent")
	
	// Create feature1
	feature1Dir := filepath.Join(intentDir, "feature1")
	os.MkdirAll(feature1Dir, 0755)
	os.WriteFile(filepath.Join(feature1Dir, "feature1.ic"), []byte("# Feature 1\n\nThis is feature 1."), 0644)
	os.WriteFile(filepath.Join(feature1Dir, "feature1.icv"), []byte("# Validations"), 0644)
	
	// Create feature2 with dependency on feature1
	feature2Dir := filepath.Join(intentDir, "feature2")
	os.MkdirAll(feature2Dir, 0755)
	os.WriteFile(filepath.Join(feature2Dir, "feature2.ic"), []byte("# Feature 2\nDepends On: feature1\n\nThis is feature 2."), 0644)
	
	// Create feature3 with dependency on feature2
	feature3Dir := filepath.Join(intentDir, "feature3")
	os.MkdirAll(feature3Dir, 0755)
	os.WriteFile(filepath.Join(feature3Dir, "feature3.ic"), []byte("# Feature 3\nDepends On: feature2\n\nThis is feature 3."), 0644)
	
	return tmpDir
}

func TestCleaner_Clean_SingleTarget(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockState := newMockStateManager()
	
	// Simulate that feature1 was built and generated files
	generatedFile := filepath.Join(projectRoot, "src", "feature1.go")
	os.MkdirAll(filepath.Dir(generatedFile), 0755)
	os.WriteFile(generatedFile, []byte("package main"), 0644)
	
	mockState.buildResults["feature1"] = &src.BuildResult{
		Target: "feature1",
		Files:  []string{"src/feature1.go"},
	}
	mockState.statuses["feature1"] = src.TargetStatusBuilt
	
	cleaner := NewCleaner(projectRoot, mockState)
	
	err := cleaner.Clean(context.Background(), CleanOptions{
		Target: "feature1",
	})
	
	if err != nil {
		t.Fatalf("Clean failed: %v", err)
	}
	
	// Check that file was removed
	if _, err := os.Stat(generatedFile); !os.IsNotExist(err) {
		t.Error("Expected generated file to be removed")
	}
	
	// Check that status was updated
	status, _ := mockState.GetTargetStatus(context.Background(), "feature1")
	if status != src.TargetStatusPending {
		t.Errorf("Expected target status to be pending, got %s", status)
	}
}

func TestCleaner_Clean_WithDependents(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockState := newMockStateManager()
	
	// Simulate that all features were built
	for i, feature := range []string{"feature1", "feature2", "feature3"} {
		generatedFile := filepath.Join(projectRoot, "src", feature+".go")
		os.MkdirAll(filepath.Dir(generatedFile), 0755)
		os.WriteFile(generatedFile, []byte("package main"), 0644)
		
		mockState.buildResults[feature] = &src.BuildResult{
			Target: feature,
			Files:  []string{"src/" + feature + ".go"},
		}
		mockState.statuses[feature] = src.TargetStatusBuilt
		
		// Add a small delay to test ordering
		_ = i
	}
	
	cleaner := NewCleaner(projectRoot, mockState)
	
	// Clean feature1, which should also clean feature2 and feature3
	err := cleaner.Clean(context.Background(), CleanOptions{
		Target: "feature1",
	})
	
	if err != nil {
		t.Fatalf("Clean failed: %v", err)
	}
	
	// Check that all dependent targets were cleaned
	for _, feature := range []string{"feature1", "feature2", "feature3"} {
		generatedFile := filepath.Join(projectRoot, "src", feature+".go")
		if _, err := os.Stat(generatedFile); !os.IsNotExist(err) {
			t.Errorf("Expected %s to be removed", generatedFile)
		}
		
		status, _ := mockState.GetTargetStatus(context.Background(), feature)
		if status != src.TargetStatusPending {
			t.Errorf("Expected %s status to be pending, got %s", feature, status)
		}
	}
}

func TestCleaner_Clean_AllTargets(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockState := newMockStateManager()
	
	// Simulate that some features were built
	for _, feature := range []string{"feature1", "feature3"} {
		generatedFile := filepath.Join(projectRoot, "src", feature+".go")
		os.MkdirAll(filepath.Dir(generatedFile), 0755)
		os.WriteFile(generatedFile, []byte("package main"), 0644)
		
		mockState.buildResults[feature] = &src.BuildResult{
			Target: feature,
			Files:  []string{"src/" + feature + ".go"},
		}
		mockState.statuses[feature] = src.TargetStatusBuilt
	}
	
	cleaner := NewCleaner(projectRoot, mockState)
	
	// Clean all targets
	err := cleaner.Clean(context.Background(), CleanOptions{})
	
	if err != nil {
		t.Fatalf("Clean failed: %v", err)
	}
	
	// Check that all built targets were cleaned
	for _, feature := range []string{"feature1", "feature3"} {
		generatedFile := filepath.Join(projectRoot, "src", feature+".go")
		if _, err := os.Stat(generatedFile); !os.IsNotExist(err) {
			t.Errorf("Expected %s to be removed", generatedFile)
		}
	}
}

func TestCleaner_Clean_DryRun(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockState := newMockStateManager()
	
	// Simulate that feature1 was built
	generatedFile := filepath.Join(projectRoot, "src", "feature1.go")
	os.MkdirAll(filepath.Dir(generatedFile), 0755)
	os.WriteFile(generatedFile, []byte("package main"), 0644)
	
	mockState.buildResults["feature1"] = &src.BuildResult{
		Target: "feature1",
		Files:  []string{"src/feature1.go"},
	}
	mockState.statuses["feature1"] = src.TargetStatusBuilt
	
	cleaner := NewCleaner(projectRoot, mockState)
	
	err := cleaner.Clean(context.Background(), CleanOptions{
		Target: "feature1",
		DryRun: true,
	})
	
	if err != nil {
		t.Fatalf("Clean failed: %v", err)
	}
	
	// Check that file was NOT removed
	if _, err := os.Stat(generatedFile); os.IsNotExist(err) {
		t.Error("Expected generated file to still exist in dry run mode")
	}
	
	// Check that status was NOT updated
	status, _ := mockState.GetTargetStatus(context.Background(), "feature1")
	if status != src.TargetStatusBuilt {
		t.Errorf("Expected target status to still be built in dry run mode, got %s", status)
	}
}

func TestCleaner_Clean_NonExistentTarget(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockState := newMockStateManager()
	
	cleaner := NewCleaner(projectRoot, mockState)
	
	err := cleaner.Clean(context.Background(), CleanOptions{
		Target: "nonexistent",
	})
	
	if err == nil {
		t.Fatal("Expected error for non-existent target")
	}
	
	if err.Error() != "target nonexistent not found" {
		t.Errorf("Expected 'target not found' error, got: %v", err)
	}
}

func TestCleaner_Clean_NoBuiltTarget(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockState := newMockStateManager()
	
	cleaner := NewCleaner(projectRoot, mockState)
	
	// Clean a target that was never built
	err := cleaner.Clean(context.Background(), CleanOptions{
		Target: "feature1",
	})
	
	if err != nil {
		t.Fatalf("Clean failed: %v", err)
	}
	
	// Should succeed without errors
}