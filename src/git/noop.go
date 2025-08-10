package git

import (
	"context"
	"time"
)

// NoOpGitManager is a no-op implementation of GitManager for when git is not available
type NoOpGitManager struct {
	projectRoot string
}

// NewNoOpGitManager creates a new no-op git manager
func NewNoOpGitManager(projectRoot string) GitManager {
	return &NoOpGitManager{
		projectRoot: projectRoot,
	}
}

func (g *NoOpGitManager) Initialize(ctx context.Context, path string) error {
	g.projectRoot = path
	return nil
}

func (g *NoOpGitManager) IsGitRepo(ctx context.Context, path string) (bool, error) {
	return false, nil
}

func (g *NoOpGitManager) Add(ctx context.Context, files []string) error {
	// No-op: silently succeed
	return nil
}

func (g *NoOpGitManager) Commit(ctx context.Context, message string) error {
	// No-op: silently succeed
	return nil
}

func (g *NoOpGitManager) GetCurrentBranch(ctx context.Context) (string, error) {
	return "main", nil
}

func (g *NoOpGitManager) GetCommitHash(ctx context.Context) (string, error) {
	return "no-git-" + time.Now().Format("20060102150405"), nil
}

func (g *NoOpGitManager) CheckoutCommit(ctx context.Context, commitHash string) error {
	// No-op: silently succeed
	return nil
}

func (g *NoOpGitManager) CreateBranch(ctx context.Context, branchName string) error {
	// No-op: silently succeed
	return nil
}

func (g *NoOpGitManager) GetStatus(ctx context.Context) (*GitStatus, error) {
	return &GitStatus{
		Branch:         "main",
		Clean:          true,
		StagedFiles:    []string{},
		ModifiedFiles:  []string{},
		UntrackedFiles: []string{},
	}, nil
}

func (g *NoOpGitManager) GetLog(ctx context.Context, limit int) ([]*GitCommit, error) {
	return []*GitCommit{}, nil
}