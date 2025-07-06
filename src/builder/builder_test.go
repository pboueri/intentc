package builder

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/agent"
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
	key := result.Target + "-" + result.GenerationID
	m.buildResults[key] = result
	return nil
}

func (m *mockStateManager) GetBuildResult(ctx context.Context, target string, generationID string) (*src.BuildResult, error) {
	key := target + "-" + generationID
	return m.buildResults[key], nil
}

func (m *mockStateManager) GetLatestBuildResult(ctx context.Context, target string) (*src.BuildResult, error) {
	var latest *src.BuildResult
	for _, result := range m.buildResults {
		if result.Target == target {
			if latest == nil || result.GeneratedAt.After(latest.GeneratedAt) {
				latest = result
			}
		}
	}
	return latest, nil
}

func (m *mockStateManager) ListBuildResults(ctx context.Context, target string) ([]*src.BuildResult, error) {
	var results []*src.BuildResult
	for _, result := range m.buildResults {
		if result.Target == target {
			results = append(results, result)
		}
	}
	return results, nil
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
	
	feature1Intent := `# Feature 1

This is feature 1 intent.`
	os.WriteFile(filepath.Join(feature1Dir, "feature1.ic"), []byte(feature1Intent), 0644)
	
	feature1Validation := `# Validations for Feature 1

## File Existence Check
Type: FileCheck
Parameters:
  file: "src/feature1.go"
  exists: true`
	os.WriteFile(filepath.Join(feature1Dir, "feature1.icv"), []byte(feature1Validation), 0644)
	
	// Create feature2 with dependency on feature1
	feature2Dir := filepath.Join(intentDir, "feature2")
	os.MkdirAll(feature2Dir, 0755)
	
	feature2Intent := `# Feature 2
Depends On: feature1

This is feature 2 intent.`
	os.WriteFile(filepath.Join(feature2Dir, "feature2.ic"), []byte(feature2Intent), 0644)
	
	return tmpDir
}

func TestBuilder_Build_SingleTarget(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockAgent := agent.NewMockAgent("test-agent")
	mockState := newMockStateManager()
	
	buildCalled := false
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		buildCalled = true
		if buildCtx.Intent.Name != "Feature 1" {
			t.Errorf("Expected intent name Feature 1, got %s", buildCtx.Intent.Name)
		}
		return []string{"src/feature1.go"}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState)
	
	err := builder.Build(context.Background(), BuildOptions{
		Target: "feature1",
	})
	
	if err != nil {
		t.Fatalf("Build failed: %v", err)
	}
	
	if !buildCalled {
		t.Error("Expected build to be called")
	}
	
	status, _ := mockState.GetTargetStatus(context.Background(), "feature1")
	if status != src.TargetStatusBuilt {
		t.Errorf("Expected target status to be built, got %s", status)
	}
}

func TestBuilder_Build_WithDependencies(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockAgent := agent.NewMockAgent("test-agent")
	mockState := newMockStateManager()
	
	buildOrder := []string{}
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		buildOrder = append(buildOrder, buildCtx.Intent.Name)
		return []string{fmt.Sprintf("src/%s.go", buildCtx.Intent.Name)}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState)
	
	err := builder.Build(context.Background(), BuildOptions{
		Target: "feature2",
	})
	
	if err != nil {
		t.Fatalf("Build failed: %v", err)
	}
	
	if len(buildOrder) != 2 {
		t.Errorf("Expected 2 builds, got %d", len(buildOrder))
	}
	
	if buildOrder[0] != "Feature 1" {
		t.Errorf("Expected Feature 1 to be built first, got %s", buildOrder[0])
	}
	
	if buildOrder[1] != "Feature 2" {
		t.Errorf("Expected Feature 2 to be built second, got %s", buildOrder[1])
	}
}

func TestBuilder_Build_AllTargets(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockAgent := agent.NewMockAgent("test-agent")
	mockState := newMockStateManager()
	
	builtTargets := make(map[string]bool)
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		builtTargets[buildCtx.Intent.Name] = true
		return []string{fmt.Sprintf("src/%s.go", buildCtx.Intent.Name)}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState)
	
	err := builder.Build(context.Background(), BuildOptions{})
	
	if err != nil {
		t.Fatalf("Build failed: %v", err)
	}
	
	if !builtTargets["Feature 1"] {
		t.Error("Expected Feature 1 to be built")
	}
	
	if !builtTargets["Feature 2"] {
		t.Error("Expected Feature 2 to be built")
	}
}

func TestBuilder_Build_SkipAlreadyBuilt(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockAgent := agent.NewMockAgent("test-agent")
	mockState := newMockStateManager()
	
	// Mark feature1 as already built
	mockState.UpdateTargetStatus(context.Background(), "feature1", src.TargetStatusBuilt)
	
	buildCalled := false
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		if buildCtx.Intent.Name == "Feature 1" {
			buildCalled = true
		}
		return []string{fmt.Sprintf("src/%s.go", buildCtx.Intent.Name)}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState)
	
	err := builder.Build(context.Background(), BuildOptions{
		Target: "feature1",
	})
	
	if err != nil {
		t.Fatalf("Build failed: %v", err)
	}
	
	if buildCalled {
		t.Error("Expected feature1 build to be skipped")
	}
}

func TestBuilder_Build_ForceRebuild(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockAgent := agent.NewMockAgent("test-agent")
	mockState := newMockStateManager()
	
	// Mark feature1 as already built
	mockState.UpdateTargetStatus(context.Background(), "feature1", src.TargetStatusBuilt)
	
	buildCalled := false
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		if buildCtx.Intent.Name == "Feature 1" {
			buildCalled = true
		}
		return []string{fmt.Sprintf("src/%s.go", buildCtx.Intent.Name)}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState)
	
	err := builder.Build(context.Background(), BuildOptions{
		Target: "feature1",
		Force:  true,
	})
	
	if err != nil {
		t.Fatalf("Build failed: %v", err)
	}
	
	if !buildCalled {
		t.Error("Expected feature1 to be rebuilt with force option")
	}
}

func TestBuilder_Build_DryRun(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockAgent := agent.NewMockAgent("test-agent")
	mockState := newMockStateManager()
	
	buildCalled := false
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		buildCalled = true
		return []string{}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState)
	
	err := builder.Build(context.Background(), BuildOptions{
		Target: "feature1",
		DryRun: true,
	})
	
	if err != nil {
		t.Fatalf("Build failed: %v", err)
	}
	
	if buildCalled {
		t.Error("Expected build not to be called in dry run mode")
	}
}

func TestBuilder_CyclicDependencyDetection(t *testing.T) {
	tmpDir := t.TempDir()
	intentDir := filepath.Join(tmpDir, "intent")
	
	// Create feature1 with dependency on feature2
	feature1Dir := filepath.Join(intentDir, "feature1")
	os.MkdirAll(feature1Dir, 0755)
	os.WriteFile(filepath.Join(feature1Dir, "feature1.ic"), []byte(`# Feature 1
Depends On: feature2`), 0644)
	
	// Create feature2 with dependency on feature1 (cycle)
	feature2Dir := filepath.Join(intentDir, "feature2")
	os.MkdirAll(feature2Dir, 0755)
	os.WriteFile(filepath.Join(feature2Dir, "feature2.ic"), []byte(`# Feature 2
Depends On: feature1`), 0644)
	
	mockAgent := agent.NewMockAgent("test-agent")
	mockState := newMockStateManager()
	
	builder := NewBuilder(tmpDir, mockAgent, mockState)
	
	err := builder.Build(context.Background(), BuildOptions{
		Target: "feature1",
	})
	
	if err == nil {
		t.Fatal("Expected error for cyclic dependency")
	}
	
	if err.Error() != "failed to build dependency graph: dependency cycle detected" {
		t.Errorf("Expected cycle detection error, got: %v", err)
	}
}