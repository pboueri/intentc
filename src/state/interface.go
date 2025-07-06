package state

import (
	"context"
	"github.com/pboueri/intentc/src"
)

type StateManager interface {
	Initialize(ctx context.Context) error
	SaveBuildResult(ctx context.Context, result *src.BuildResult) error
	GetBuildResult(ctx context.Context, target string, generationID string) (*src.BuildResult, error)
	GetLatestBuildResult(ctx context.Context, target string) (*src.BuildResult, error)
	ListBuildResults(ctx context.Context, target string) ([]*src.BuildResult, error)
	CommitChanges(ctx context.Context, message string, files []string) error
	GetTargetStatus(ctx context.Context, target string) (src.TargetStatus, error)
	UpdateTargetStatus(ctx context.Context, target string, status src.TargetStatus) error
}
