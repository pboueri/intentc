package state

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/git"
)

type mockGit struct {
	addCalled    bool
	commitCalled bool
	addFiles     []string
	commitMsg    string
}

func (m *mockGit) Initialize(ctx context.Context, dir string) error {
	return nil
}

func (m *mockGit) Add(ctx context.Context, files []string) error {
	m.addCalled = true
	m.addFiles = files
	return nil
}

func (m *mockGit) Commit(ctx context.Context, message string) error {
	m.commitCalled = true
	m.commitMsg = message
	return nil
}

func (m *mockGit) IsGitRepo(ctx context.Context, path string) (bool, error) {
	return true, nil
}

func (m *mockGit) GetCurrentBranch(ctx context.Context) (string, error) {
	return "main", nil
}

func (m *mockGit) GetCommitHash(ctx context.Context) (string, error) {
	return "abc123", nil
}

func (m *mockGit) CheckoutCommit(ctx context.Context, commitHash string) error {
	return nil
}

func (m *mockGit) CreateBranch(ctx context.Context, branchName string) error {
	return nil
}

func (m *mockGit) GetStatus(ctx context.Context) (*git.GitStatus, error) {
	return &git.GitStatus{}, nil
}

func (m *mockGit) GetLog(ctx context.Context, limit int) ([]*git.GitCommit, error) {
	return []*git.GitCommit{}, nil
}

func TestGitStateManager_Initialize(t *testing.T) {
	tmpDir := t.TempDir()
	mockGit := &mockGit{}
	manager := NewGitStateManager(mockGit, tmpDir)

	err := manager.Initialize(context.Background())
	if err != nil {
		t.Fatalf("Initialize failed: %v", err)
	}

	stateDir := filepath.Join(tmpDir, ".intentc", "state")
	if _, err := os.Stat(stateDir); os.IsNotExist(err) {
		t.Errorf("State directory was not created")
	}

	statusFile := filepath.Join(stateDir, "status.json")
	if _, err := os.Stat(statusFile); os.IsNotExist(err) {
		t.Errorf("Status file was not created")
	}
}

func TestGitStateManager_SaveAndGetBuildResult(t *testing.T) {
	tmpDir := t.TempDir()
	mockGit := &mockGit{}
	manager := NewGitStateManager(mockGit, tmpDir)

	ctx := context.Background()
	if err := manager.Initialize(ctx); err != nil {
		t.Fatalf("Initialize failed: %v", err)
	}

	result := &src.BuildResult{
		Target:       "test-target",
		GenerationID: "gen-123",
		Success:      true,
		Error:        nil,
		GeneratedAt:  time.Now(),
		Files:        []string{"file1.go", "file2.go"},
	}

	if err := manager.SaveBuildResult(ctx, result); err != nil {
		t.Fatalf("SaveBuildResult failed: %v", err)
	}

	retrieved, err := manager.GetBuildResult(ctx, "test-target", "gen-123")
	if err != nil {
		t.Fatalf("GetBuildResult failed: %v", err)
	}

	if retrieved.Target != result.Target {
		t.Errorf("Expected target %s, got %s", result.Target, retrieved.Target)
	}
	if retrieved.GenerationID != result.GenerationID {
		t.Errorf("Expected generation ID %s, got %s", result.GenerationID, retrieved.GenerationID)
	}
	if retrieved.Success != result.Success {
		t.Errorf("Expected success %v, got %v", result.Success, retrieved.Success)
	}
	if len(retrieved.Files) != len(result.Files) {
		t.Errorf("Expected %d files, got %d", len(result.Files), len(retrieved.Files))
	}
}

func TestGitStateManager_GetLatestBuildResult(t *testing.T) {
	tmpDir := t.TempDir()
	mockGit := &mockGit{}
	manager := NewGitStateManager(mockGit, tmpDir)

	ctx := context.Background()
	if err := manager.Initialize(ctx); err != nil {
		t.Fatalf("Initialize failed: %v", err)
	}

	result1 := &src.BuildResult{
		Target:       "test-target",
		GenerationID: "gen-123",
		Success:      true,
		GeneratedAt:  time.Now(),
	}

	result2 := &src.BuildResult{
		Target:       "test-target",
		GenerationID: "gen-456",
		Success:      true,
		GeneratedAt:  time.Now().Add(time.Hour),
	}

	if err := manager.SaveBuildResult(ctx, result1); err != nil {
		t.Fatalf("SaveBuildResult failed: %v", err)
	}
	if err := manager.SaveBuildResult(ctx, result2); err != nil {
		t.Fatalf("SaveBuildResult failed: %v", err)
	}

	latest, err := manager.GetLatestBuildResult(ctx, "test-target")
	if err != nil {
		t.Fatalf("GetLatestBuildResult failed: %v", err)
	}

	if latest.GenerationID != result2.GenerationID {
		t.Errorf("Expected latest generation ID %s, got %s", result2.GenerationID, latest.GenerationID)
	}
}

func TestGitStateManager_ListBuildResults(t *testing.T) {
	tmpDir := t.TempDir()
	mockGit := &mockGit{}
	manager := NewGitStateManager(mockGit, tmpDir)

	ctx := context.Background()
	if err := manager.Initialize(ctx); err != nil {
		t.Fatalf("Initialize failed: %v", err)
	}

	results := []*src.BuildResult{
		{
			Target:       "test-target",
			GenerationID: "gen-123",
			Success:      true,
			GeneratedAt:  time.Now(),
		},
		{
			Target:       "test-target",
			GenerationID: "gen-456",
			Success:      false,
			GeneratedAt:  time.Now().Add(time.Hour),
		},
		{
			Target:       "test-target",
			GenerationID: "gen-789",
			Success:      true,
			GeneratedAt:  time.Now().Add(2 * time.Hour),
		},
	}

	for _, result := range results {
		if err := manager.SaveBuildResult(ctx, result); err != nil {
			t.Fatalf("SaveBuildResult failed: %v", err)
		}
	}

	listed, err := manager.ListBuildResults(ctx, "test-target")
	if err != nil {
		t.Fatalf("ListBuildResults failed: %v", err)
	}

	if len(listed) != len(results) {
		t.Errorf("Expected %d results, got %d", len(results), len(listed))
	}
}

func TestGitStateManager_CommitChanges(t *testing.T) {
	tmpDir := t.TempDir()
	mockGit := &mockGit{}
	manager := NewGitStateManager(mockGit, tmpDir)

	ctx := context.Background()
	files := []string{"file1.go", "file2.go"}
	message := "Test commit"

	err := manager.CommitChanges(ctx, message, files)
	if err != nil {
		t.Fatalf("CommitChanges failed: %v", err)
	}

	if !mockGit.addCalled {
		t.Errorf("Expected git.Add to be called")
	}
	if !mockGit.commitCalled {
		t.Errorf("Expected git.Commit to be called")
	}
	if mockGit.commitMsg != message {
		t.Errorf("Expected commit message %s, got %s", message, mockGit.commitMsg)
	}
	if len(mockGit.addFiles) != len(files) {
		t.Errorf("Expected %d files to be added, got %d", len(files), len(mockGit.addFiles))
	}
}

func TestGitStateManager_TargetStatus(t *testing.T) {
	tmpDir := t.TempDir()
	mockGit := &mockGit{}
	manager := NewGitStateManager(mockGit, tmpDir)

	ctx := context.Background()
	if err := manager.Initialize(ctx); err != nil {
		t.Fatalf("Initialize failed: %v", err)
	}

	status, err := manager.GetTargetStatus(ctx, "new-target")
	if err != nil {
		t.Fatalf("GetTargetStatus failed: %v", err)
	}
	if status != src.TargetStatusPending {
		t.Errorf("Expected status %s for new target, got %s", src.TargetStatusPending, status)
	}

	if err := manager.UpdateTargetStatus(ctx, "test-target", src.TargetStatusBuilt); err != nil {
		t.Fatalf("UpdateTargetStatus failed: %v", err)
	}

	status, err = manager.GetTargetStatus(ctx, "test-target")
	if err != nil {
		t.Fatalf("GetTargetStatus failed: %v", err)
	}
	if status != src.TargetStatusBuilt {
		t.Errorf("Expected status %s, got %s", src.TargetStatusBuilt, status)
	}
}