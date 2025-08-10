package cmd

import (
	"context"

	"github.com/pboueri/intentc/src/git"
	"github.com/pboueri/intentc/src/logger"
	"github.com/pboueri/intentc/src/state"
)

// CreateManagers creates appropriate git and state managers based on git availability
func CreateManagers(ctx context.Context, projectRoot string) (git.GitManager, state.StateManager, error) {
	// Try to create a git manager and check if git is available
	gitMgr := git.NewGitManager(projectRoot)
	isGitRepo, err := gitMgr.IsGitRepo(ctx, projectRoot)
	
	// If there's an error checking git or it's not a git repo, use no-op managers
	if err != nil || !isGitRepo {
		if err != nil {
			logger.Info("Git not available, using file-based state tracking")
		} else {
			logger.Info("No git repository detected, using file-based state tracking")
		}
		
		// Use no-op managers
		noOpGitMgr := git.NewNoOpGitManager(projectRoot)
		noOpStateMgr := state.NewNoOpStateManager(projectRoot)
		
		// Initialize the no-op git manager
		if err := noOpGitMgr.Initialize(ctx, projectRoot); err != nil {
			return nil, nil, err
		}
		
		return noOpGitMgr, noOpStateMgr, nil
	}
	
	logger.Info("Git repository detected, using git-based state tracking")
	
	// Initialize git manager
	if err := gitMgr.Initialize(ctx, projectRoot); err != nil {
		return nil, nil, err
	}
	
	// Create git-based state manager
	gitStateMgr := state.NewGitStateManager(gitMgr, projectRoot)
	
	return gitMgr, gitStateMgr, nil
}