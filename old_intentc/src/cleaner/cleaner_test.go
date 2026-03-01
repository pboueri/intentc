package cleaner

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/pboueri/intentc/src"
)

type mockStateManager struct {
	statuses          map[string]src.TargetStatus
	buildResults      map[string]*src.BuildResult
	buildStatuses     map[string]map[string]src.TargetStatus // buildName -> target -> status
	buildBuildResults map[string]map[string]*src.BuildResult   // buildName -> target -> result
}

func newMockStateManager() *mockStateManager {
	return &mockStateManager{
		statuses:          make(map[string]src.TargetStatus),
		buildResults:      make(map[string]*src.BuildResult),
		buildStatuses:     make(map[string]map[string]src.TargetStatus),
		buildBuildResults: make(map[string]map[string]*src.BuildResult),
	}
}

func (m *mockStateManager) Initialize(ctx context.Context) error {
	return nil
}

func (m *mockStateManager) SaveBuildResult(ctx context.Context, result *src.BuildResult) error {
	m.buildResults[result.Target] = result
	
	// Also store in build-specific map if BuildName is set
	if result.BuildName != "" {
		if _, ok := m.buildBuildResults[result.BuildName]; !ok {
			m.buildBuildResults[result.BuildName] = make(map[string]*src.BuildResult)
		}
		m.buildBuildResults[result.BuildName][result.Target] = result
	}
	
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

// New build-aware methods
func (m *mockStateManager) GetTargetStatusForBuild(ctx context.Context, target string, buildName string) (src.TargetStatus, error) {
	if buildStatuses, ok := m.buildStatuses[buildName]; ok {
		if status, ok := buildStatuses[target]; ok {
			return status, nil
		}
	}
	return src.TargetStatusPending, nil
}

func (m *mockStateManager) UpdateTargetStatusForBuild(ctx context.Context, target string, buildName string, status src.TargetStatus) error {
	if _, ok := m.buildStatuses[buildName]; !ok {
		m.buildStatuses[buildName] = make(map[string]src.TargetStatus)
	}
	m.buildStatuses[buildName][target] = status
	return nil
}

func (m *mockStateManager) GetLatestBuildResultForBuild(ctx context.Context, target string, buildName string) (*src.BuildResult, error) {
	if buildResults, ok := m.buildBuildResults[buildName]; ok {
		return buildResults[target], nil
	}
	return nil, nil
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

// Tests for build directory cleaning
func TestCleaner_CleanBuildDirectory(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockState := newMockStateManager()
	
	// Create build directories
	buildDir := filepath.Join(projectRoot, "build-test-build")
	os.MkdirAll(buildDir, 0755)
	
	// Create some files in the build directory
	testFile1 := filepath.Join(buildDir, "file1.go")
	testFile2 := filepath.Join(buildDir, "subdir", "file2.js")
	os.WriteFile(testFile1, []byte("test content"), 0644)
	os.MkdirAll(filepath.Dir(testFile2), 0755)
	os.WriteFile(testFile2, []byte("test content 2"), 0644)
	
	cleaner := NewCleaner(projectRoot, mockState)
	
	// Clean specific build directory
	err := cleaner.Clean(context.Background(), CleanOptions{
		BuildName: "test-build",
	})
	
	if err != nil {
		t.Fatalf("Clean failed: %v", err)
	}
	
	// Verify build directory was removed
	if _, err := os.Stat(buildDir); !os.IsNotExist(err) {
		t.Error("Expected build directory to be removed")
	}
}

func TestCleaner_CleanBuildDirectory_NonExistent(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockState := newMockStateManager()
	
	cleaner := NewCleaner(projectRoot, mockState)
	
	// Try to clean non-existent build directory
	err := cleaner.Clean(context.Background(), CleanOptions{
		BuildName: "nonexistent-build",
	})
	
	// Should succeed without error
	if err != nil {
		t.Fatalf("Clean failed: %v", err)
	}
}

func TestCleaner_CleanBuildDirectory_DryRun(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockState := newMockStateManager()
	
	// Create build directory
	buildDir := filepath.Join(projectRoot, "build-dry-run-test")
	os.MkdirAll(buildDir, 0755)
	testFile := filepath.Join(buildDir, "test.txt")
	os.WriteFile(testFile, []byte("test"), 0644)
	
	cleaner := NewCleaner(projectRoot, mockState)
	
	// Clean with dry run
	err := cleaner.Clean(context.Background(), CleanOptions{
		BuildName: "dry-run-test",
		DryRun:    true,
	})
	
	if err != nil {
		t.Fatalf("Clean failed: %v", err)
	}
	
	// Verify build directory still exists
	if _, err := os.Stat(buildDir); os.IsNotExist(err) {
		t.Error("Expected build directory to still exist in dry run mode")
	}
	
	// Verify test file still exists
	if _, err := os.Stat(testFile); os.IsNotExist(err) {
		t.Error("Expected test file to still exist in dry run mode")
	}
}

func TestCleaner_CleanMultipleBuildDirectories(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockState := newMockStateManager()
	
	// Create multiple build directories
	buildNames := []string{"dev", "staging", "prod"}
	for _, name := range buildNames {
		buildDir := filepath.Join(projectRoot, "build-"+name)
		os.MkdirAll(buildDir, 0755)
		testFile := filepath.Join(buildDir, "app.js")
		os.WriteFile(testFile, []byte(name), 0644)
	}
	
	cleaner := NewCleaner(projectRoot, mockState)
	
	// Clean only staging build
	err := cleaner.Clean(context.Background(), CleanOptions{
		BuildName: "staging",
	})
	
	if err != nil {
		t.Fatalf("Clean failed: %v", err)
	}
	
	// Verify only staging was removed
	for _, name := range buildNames {
		buildDir := filepath.Join(projectRoot, "build-"+name)
		if name == "staging" {
			if _, err := os.Stat(buildDir); !os.IsNotExist(err) {
				t.Errorf("Expected %s build directory to be removed", name)
			}
		} else {
			if _, err := os.Stat(buildDir); os.IsNotExist(err) {
				t.Errorf("Expected %s build directory to still exist", name)
			}
		}
	}
}