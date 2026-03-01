package git

import (
	"context"
	"time"
)

type GitManager interface {
	Initialize(ctx context.Context, path string) error
	IsGitRepo(ctx context.Context, path string) (bool, error)
	Add(ctx context.Context, files []string) error
	Commit(ctx context.Context, message string) error
	GetCurrentBranch(ctx context.Context) (string, error)
	GetCommitHash(ctx context.Context) (string, error)
	CheckoutCommit(ctx context.Context, commitHash string) error
	CreateBranch(ctx context.Context, branchName string) error
	GetStatus(ctx context.Context) (*GitStatus, error)
	GetLog(ctx context.Context, limit int) ([]*GitCommit, error)
}

type GitStatus struct {
	Branch      string
	Clean       bool
	StagedFiles []string
	ModifiedFiles []string
	UntrackedFiles []string
}

type GitCommit struct {
	Hash      string
	Author    string
	Date      time.Time
	Message   string
	Files     []string
}
