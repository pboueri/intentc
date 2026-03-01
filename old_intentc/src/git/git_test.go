package git

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func setupTestRepo(t *testing.T) (string, func()) {
	tmpDir, err := os.MkdirTemp("", "git-test")
	require.NoError(t, err)

	cleanup := func() {
		os.RemoveAll(tmpDir)
	}

	return tmpDir, cleanup
}

func TestGitManager_Initialize(t *testing.T) {
	tmpDir, cleanup := setupTestRepo(t)
	defer cleanup()

	ctx := context.Background()
	mgr := NewGitManager(tmpDir)

	err := mgr.Initialize(ctx, tmpDir)
	require.NoError(t, err)

	gitDir := filepath.Join(tmpDir, ".git")
	_, err = os.Stat(gitDir)
	assert.NoError(t, err)

	err = mgr.Initialize(ctx, tmpDir)
	assert.NoError(t, err)
}

func TestGitManager_IsGitRepo(t *testing.T) {
	tmpDir, cleanup := setupTestRepo(t)
	defer cleanup()

	ctx := context.Background()
	mgr := NewGitManager(tmpDir)

	isRepo, err := mgr.IsGitRepo(ctx, tmpDir)
	require.NoError(t, err)
	assert.False(t, isRepo)

	err = mgr.Initialize(ctx, tmpDir)
	require.NoError(t, err)

	isRepo, err = mgr.IsGitRepo(ctx, tmpDir)
	require.NoError(t, err)
	assert.True(t, isRepo)
}

func TestGitManager_AddAndCommit(t *testing.T) {
	tmpDir, cleanup := setupTestRepo(t)
	defer cleanup()

	ctx := context.Background()
	mgr := NewGitManager(tmpDir)

	err := mgr.Initialize(ctx, tmpDir)
	require.NoError(t, err)

	// Configure git for testing
	gitMgr := mgr.(*gitManager)
	_, err = gitMgr.runGitCommand(ctx, "config", "user.email", "test@example.com")
	require.NoError(t, err)
	_, err = gitMgr.runGitCommand(ctx, "config", "user.name", "Test User")
	require.NoError(t, err)

	testFile := filepath.Join(tmpDir, "test.txt")
	err = os.WriteFile(testFile, []byte("test content"), 0644)
	require.NoError(t, err)

	err = mgr.Add(ctx, []string{"test.txt"})
	require.NoError(t, err)

	err = mgr.Commit(ctx, "Test commit")
	require.NoError(t, err)

	status, err := mgr.GetStatus(ctx)
	require.NoError(t, err)
	assert.True(t, status.Clean)
}

func TestGitManager_GetStatus(t *testing.T) {
	tmpDir, cleanup := setupTestRepo(t)
	defer cleanup()

	ctx := context.Background()
	mgr := NewGitManager(tmpDir)

	err := mgr.Initialize(ctx, tmpDir)
	require.NoError(t, err)

	// Configure git for testing
	gitMgr := mgr.(*gitManager)
	_, err = gitMgr.runGitCommand(ctx, "config", "user.email", "test@example.com")
	require.NoError(t, err)
	_, err = gitMgr.runGitCommand(ctx, "config", "user.name", "Test User")
	require.NoError(t, err)

	// Create initial commit to have a valid HEAD
	testFile := filepath.Join(tmpDir, "initial.txt")
	err = os.WriteFile(testFile, []byte("initial"), 0644)
	require.NoError(t, err)
	err = mgr.Add(ctx, []string{"initial.txt"})
	require.NoError(t, err)
	err = mgr.Commit(ctx, "Initial commit")
	require.NoError(t, err)

	status, err := mgr.GetStatus(ctx)
	require.NoError(t, err)
	assert.True(t, status.Clean)
	assert.Empty(t, status.StagedFiles)
	assert.Empty(t, status.ModifiedFiles)
	assert.Empty(t, status.UntrackedFiles)

	untrackedFile := filepath.Join(tmpDir, "untracked.txt")
	err = os.WriteFile(untrackedFile, []byte("untracked content"), 0644)
	require.NoError(t, err)

	status, err = mgr.GetStatus(ctx)
	require.NoError(t, err)
	assert.False(t, status.Clean)
	assert.Len(t, status.UntrackedFiles, 1)
	assert.Contains(t, status.UntrackedFiles, "untracked.txt")
}

func TestGitManager_GetCurrentBranch(t *testing.T) {
	tmpDir, cleanup := setupTestRepo(t)
	defer cleanup()

	ctx := context.Background()
	mgr := NewGitManager(tmpDir)

	err := mgr.Initialize(ctx, tmpDir)
	require.NoError(t, err)

	// Configure git for testing
	gitMgr := mgr.(*gitManager)
	_, err = gitMgr.runGitCommand(ctx, "config", "user.email", "test@example.com")
	require.NoError(t, err)
	_, err = gitMgr.runGitCommand(ctx, "config", "user.name", "Test User")
	require.NoError(t, err)

	testFile := filepath.Join(tmpDir, "initial.txt")
	err = os.WriteFile(testFile, []byte("initial content"), 0644)
	require.NoError(t, err)
	err = mgr.Add(ctx, []string{"initial.txt"})
	require.NoError(t, err)
	err = mgr.Commit(ctx, "Initial commit")
	require.NoError(t, err)

	branch, err := mgr.GetCurrentBranch(ctx)
	require.NoError(t, err)
	assert.Contains(t, []string{"main", "master"}, branch)
}
