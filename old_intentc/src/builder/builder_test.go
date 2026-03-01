package builder

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/agent"
	"github.com/pboueri/intentc/src/config"
	"github.com/pboueri/intentc/src/git"
	"github.com/stretchr/testify/assert"
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
	key := result.Target + "-" + result.GenerationID
	m.buildResults[key] = result
	
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

// mockGitManager for tests
type mockGitManager struct{}

func (m *mockGitManager) Initialize(ctx context.Context, path string) error {
	return nil
}

func (m *mockGitManager) IsGitRepo(ctx context.Context, path string) (bool, error) {
	return true, nil
}

func (m *mockGitManager) Add(ctx context.Context, files []string) error {
	return nil
}

func (m *mockGitManager) Commit(ctx context.Context, message string) error {
	return nil
}

func (m *mockGitManager) GetCurrentBranch(ctx context.Context) (string, error) {
	return "main", nil
}

func (m *mockGitManager) GetCommitHash(ctx context.Context) (string, error) {
	return "abc123", nil
}

func (m *mockGitManager) CheckoutCommit(ctx context.Context, commitHash string) error {
	return nil
}

func (m *mockGitManager) CreateBranch(ctx context.Context, branchName string) error {
	return nil
}

func (m *mockGitManager) GetStatus(ctx context.Context) (*git.GitStatus, error) {
	return &git.GitStatus{
		Branch:         "main",
		Clean:          true,
		ModifiedFiles:  []string{},
		UntrackedFiles: []string{},
	}, nil
}

func (m *mockGitManager) GetLog(ctx context.Context, limit int) ([]*git.GitCommit, error) {
	return []*git.GitCommit{}, nil
}

func getTestConfig() *config.Config {
	return &config.Config{
		Build: config.BuildConfig{
			DefaultBuildName: "test-build",
		},
	}
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
		if buildCtx.Intent.Name != "feature1" {
			t.Errorf("Expected intent name feature1, got %s", buildCtx.Intent.Name)
		}
		return []string{"src/feature1.go"}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState, &mockGitManager{}, getTestConfig())
	
	err := builder.Build(context.Background(), BuildOptions{
		Target: "feature1",
	})
	
	if err != nil {
		t.Fatalf("Build failed: %v", err)
	}
	
	if !buildCalled {
		t.Error("Expected build to be called")
	}
	
	// Check build-specific status
	status, _ := mockState.GetTargetStatusForBuild(context.Background(), "feature1", "test-build")
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
	
	builder := NewBuilder(projectRoot, mockAgent, mockState, &mockGitManager{}, getTestConfig())
	
	err := builder.Build(context.Background(), BuildOptions{
		Target: "feature2",
	})
	
	if err != nil {
		t.Fatalf("Build failed: %v", err)
	}
	
	if len(buildOrder) != 2 {
		t.Errorf("Expected 2 builds, got %d", len(buildOrder))
	}
	
	if buildOrder[0] != "feature1" {
		t.Errorf("Expected feature1 to be built first, got %s", buildOrder[0])
	}
	
	if buildOrder[1] != "feature2" {
		t.Errorf("Expected feature2 to be built second, got %s", buildOrder[1])
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
	
	builder := NewBuilder(projectRoot, mockAgent, mockState, &mockGitManager{}, getTestConfig())
	
	err := builder.Build(context.Background(), BuildOptions{})
	
	if err != nil {
		t.Fatalf("Build failed: %v", err)
	}
	
	if !builtTargets["feature1"] {
		t.Error("Expected feature1 to be built")
	}
	
	if !builtTargets["feature2"] {
		t.Error("Expected feature2 to be built")
	}
}

func TestBuilder_Build_SkipAlreadyBuilt(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockAgent := agent.NewMockAgent("test-agent")
	mockState := newMockStateManager()
	
	// Mark feature1 as already built in the default build
	mockState.UpdateTargetStatusForBuild(context.Background(), "feature1", "test-build", src.TargetStatusBuilt)
	
	buildCalled := false
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		if buildCtx.Intent.Name == "feature1" {
			buildCalled = true
		}
		return []string{fmt.Sprintf("src/%s.go", buildCtx.Intent.Name)}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState, &mockGitManager{}, getTestConfig())
	
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
	
	// Mark feature1 as already built in the default build
	mockState.UpdateTargetStatusForBuild(context.Background(), "feature1", "test-build", src.TargetStatusBuilt)
	
	buildCalled := false
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		if buildCtx.Intent.Name == "feature1" {
			buildCalled = true
		}
		return []string{fmt.Sprintf("src/%s.go", buildCtx.Intent.Name)}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState, &mockGitManager{}, getTestConfig())
	
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
	
	builder := NewBuilder(projectRoot, mockAgent, mockState, &mockGitManager{}, getTestConfig())
	
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
	
	builder := NewBuilder(tmpDir, mockAgent, mockState, &mockGitManager{}, getTestConfig())
	
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

// Test build directory functionality
func TestBuilder_BuildDirectory_Creation(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockAgent := agent.NewMockAgent("test-agent")
	mockState := newMockStateManager()
	cfg := getTestConfig()
	
	var capturedBuildPath string
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		capturedBuildPath = buildCtx.BuildPath
		// Verify build directory exists
		if _, err := os.Stat(buildCtx.BuildPath); os.IsNotExist(err) {
			t.Errorf("Build directory does not exist: %s", buildCtx.BuildPath)
		}
		return []string{"src/feature1.go"}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState, &mockGitManager{}, cfg)
	
	err := builder.Build(context.Background(), BuildOptions{
		Target: "feature1",
	})
	
	assert.NoError(t, err)
	
	// Verify build directory was created with default name
	expectedPath := filepath.Join(projectRoot, "build-test-build")
	assert.Equal(t, expectedPath, capturedBuildPath)
	
	// Verify directory still exists after build
	_, err = os.Stat(expectedPath)
	assert.NoError(t, err)
}

func TestBuilder_BuildDirectory_CustomName(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockAgent := agent.NewMockAgent("test-agent")
	mockState := newMockStateManager()
	cfg := getTestConfig()
	
	var capturedBuildPath string
	var capturedBuildName string
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		capturedBuildPath = buildCtx.BuildPath
		capturedBuildName = buildCtx.BuildName
		return []string{"src/feature1.go"}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState, &mockGitManager{}, cfg)
	
	err := builder.Build(context.Background(), BuildOptions{
		Target:    "feature1",
		BuildName: "custom-build",
	})
	
	assert.NoError(t, err)
	
	// Verify custom build directory was used
	expectedPath := filepath.Join(projectRoot, "build-custom-build")
	assert.Equal(t, expectedPath, capturedBuildPath)
	assert.Equal(t, "custom-build", capturedBuildName)
}

func TestBuilder_BuildResult_IncludesBuildInfo(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockAgent := agent.NewMockAgent("test-agent")
	mockState := newMockStateManager()
	cfg := getTestConfig()
	
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		return []string{"src/feature1.go"}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState, &mockGitManager{}, cfg)
	
	err := builder.Build(context.Background(), BuildOptions{
		Target:    "feature1",
		BuildName: "test-run",
	})
	
	assert.NoError(t, err)
	
	// Get the saved build result
	result, err := mockState.GetLatestBuildResult(context.Background(), "feature1")
	assert.NoError(t, err)
	assert.NotNil(t, result)
	
	// Verify build info is included
	assert.Equal(t, "test-run", result.BuildName)
	expectedPath := filepath.Join(projectRoot, "build-test-run")
	assert.Equal(t, expectedPath, result.BuildPath)
}

func TestBuilder_BuildDirectory_IsolatedWorkingDir(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockAgent := agent.NewMockAgent("test-agent")
	mockState := newMockStateManager()
	cfg := getTestConfig()
	
	// Track if agent's working directory is set correctly
	workingDirCorrect := false
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		// In a real agent, the working directory would be changed
		// Here we just verify the build path is provided
		workingDirCorrect = buildCtx.BuildPath == filepath.Join(projectRoot, "build-isolated-test")
		return []string{"app.js"}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState, &mockGitManager{}, cfg)
	
	err := builder.Build(context.Background(), BuildOptions{
		Target:    "feature1",
		BuildName: "isolated-test",
	})
	
	assert.NoError(t, err)
	assert.True(t, workingDirCorrect, "Agent should receive correct build path")
}

func TestBuilder_MultipleBuildDirectories(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockAgent := agent.NewMockAgent("test-agent")
	mockState := newMockStateManager()
	cfg := getTestConfig()
	
	buildPaths := make(map[string]string)
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		buildPaths[buildCtx.BuildName] = buildCtx.BuildPath
		// Create a file in the build directory to simulate generation
		testFile := filepath.Join(buildCtx.BuildPath, "test.txt")
		os.WriteFile(testFile, []byte(buildCtx.BuildName), 0644)
		return []string{"test.txt"}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState, &mockGitManager{}, cfg)
	
	// Build with different build names
	for _, buildName := range []string{"dev", "staging", "prod"} {
		err := builder.Build(context.Background(), BuildOptions{
			Target:    "feature1",
			BuildName: buildName,
			Force:     true, // Force rebuild
		})
		assert.NoError(t, err)
	}
	
	// Verify all build directories exist and are separate
	assert.Len(t, buildPaths, 3)
	for name, path := range buildPaths {
		expectedPath := filepath.Join(projectRoot, "build-"+name)
		assert.Equal(t, expectedPath, path)
		
		// Verify the test file exists in each build directory
		testFile := filepath.Join(path, "test.txt")
		content, err := os.ReadFile(testFile)
		assert.NoError(t, err)
		assert.Equal(t, name, string(content))
	}
}

// Test that build state is tracked separately per build name
func TestBuilder_PerBuildStateTracking(t *testing.T) {
	projectRoot := setupTestProject(t)
	mockAgent := agent.NewMockAgent("test-agent")
	mockState := newMockStateManager()
	cfg := getTestConfig()
	
	buildCount := 0
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		buildCount++
		return []string{"output.txt"}, nil
	}
	
	builder := NewBuilder(projectRoot, mockAgent, mockState, &mockGitManager{}, cfg)
	
	// Build feature1 in "dev" build
	err := builder.Build(context.Background(), BuildOptions{
		Target:    "feature1",
		BuildName: "dev",
	})
	assert.NoError(t, err)
	assert.Equal(t, 1, buildCount)
	
	// Check that feature1 is built in "dev"
	status, err := mockState.GetTargetStatusForBuild(context.Background(), "feature1", "dev")
	assert.NoError(t, err)
	assert.Equal(t, src.TargetStatusBuilt, status)
	
	// Check that feature1 is NOT built in "staging"
	status, err = mockState.GetTargetStatusForBuild(context.Background(), "feature1", "staging")
	assert.NoError(t, err)
	assert.Equal(t, src.TargetStatusPending, status)
	
	// Build feature1 again in "dev" - should skip
	err = builder.Build(context.Background(), BuildOptions{
		Target:    "feature1",
		BuildName: "dev",
	})
	assert.NoError(t, err)
	assert.Equal(t, 1, buildCount) // Should not increment
	
	// Build feature1 in "staging" - should build
	err = builder.Build(context.Background(), BuildOptions{
		Target:    "feature1",
		BuildName: "staging",
	})
	assert.NoError(t, err)
	assert.Equal(t, 2, buildCount) // Should increment
	
	// Check that feature1 is now built in both
	status, err = mockState.GetTargetStatusForBuild(context.Background(), "feature1", "dev")
	assert.NoError(t, err)
	assert.Equal(t, src.TargetStatusBuilt, status)
	
	status, err = mockState.GetTargetStatusForBuild(context.Background(), "feature1", "staging")
	assert.NoError(t, err)
	assert.Equal(t, src.TargetStatusBuilt, status)
	
	// Verify build results are stored separately
	devResult, err := mockState.GetLatestBuildResultForBuild(context.Background(), "feature1", "dev")
	assert.NoError(t, err)
	assert.NotNil(t, devResult)
	assert.Equal(t, "dev", devResult.BuildName)
	
	stagingResult, err := mockState.GetLatestBuildResultForBuild(context.Background(), "feature1", "staging")
	assert.NoError(t, err)
	assert.NotNil(t, stagingResult)
	assert.Equal(t, "staging", stagingResult.BuildName)
}