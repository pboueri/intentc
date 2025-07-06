package git

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type gitManager struct {
	repoPath string
}

func New() GitManager {
	return &gitManager{}
}

func NewGitManager(repoPath string) GitManager {
	return &gitManager{
		repoPath: repoPath,
	}
}

func (g *gitManager) runGitCommand(ctx context.Context, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Dir = g.repoPath
	output, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("git command failed: %v, output: %s", err, string(output))
	}
	return strings.TrimSpace(string(output)), nil
}

func (g *gitManager) Initialize(ctx context.Context, path string) error {
	g.repoPath = path
	if _, err := os.Stat(filepath.Join(path, ".git")); os.IsNotExist(err) {
		_, err := g.runGitCommand(ctx, "init")
		return err
	}
	return nil
}

func (g *gitManager) IsGitRepo(ctx context.Context, path string) (bool, error) {
	_, err := os.Stat(filepath.Join(path, ".git"))
	if os.IsNotExist(err) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return true, nil
}

func (g *gitManager) Add(ctx context.Context, files []string) error {
	args := append([]string{"add"}, files...)
	_, err := g.runGitCommand(ctx, args...)
	return err
}

func (g *gitManager) Commit(ctx context.Context, message string) error {
	_, err := g.runGitCommand(ctx, "commit", "-m", message)
	return err
}

func (g *gitManager) GetCurrentBranch(ctx context.Context) (string, error) {
	return g.runGitCommand(ctx, "rev-parse", "--abbrev-ref", "HEAD")
}

func (g *gitManager) GetCommitHash(ctx context.Context) (string, error) {
	return g.runGitCommand(ctx, "rev-parse", "HEAD")
}

func (g *gitManager) CheckoutCommit(ctx context.Context, commitHash string) error {
	_, err := g.runGitCommand(ctx, "checkout", commitHash)
	return err
}

func (g *gitManager) CreateBranch(ctx context.Context, branchName string) error {
	_, err := g.runGitCommand(ctx, "checkout", "-b", branchName)
	return err
}

func (g *gitManager) GetStatus(ctx context.Context) (*GitStatus, error) {
	branch, err := g.GetCurrentBranch(ctx)
	if err != nil {
		return nil, err
	}

	output, err := g.runGitCommand(ctx, "status", "--porcelain")
	if err != nil {
		return nil, err
	}

	status := &GitStatus{
		Branch:         branch,
		Clean:          output == "",
		StagedFiles:    []string{},
		ModifiedFiles:  []string{},
		UntrackedFiles: []string{},
	}

	if output != "" {
		lines := strings.Split(output, "\n")
		for _, line := range lines {
			if len(line) < 3 {
				continue
			}
			statusCode := line[:2]
			file := strings.TrimSpace(line[3:])
			
			switch {
			case strings.HasPrefix(statusCode, "A") || strings.HasPrefix(statusCode, "M"):
				status.StagedFiles = append(status.StagedFiles, file)
			case strings.Contains(statusCode, "M"):
				status.ModifiedFiles = append(status.ModifiedFiles, file)
			case statusCode == "??":
				status.UntrackedFiles = append(status.UntrackedFiles, file)
			}
		}
	}

	return status, nil
}

func (g *gitManager) GetLog(ctx context.Context, limit int) ([]*GitCommit, error) {
	output, err := g.runGitCommand(ctx, "log", fmt.Sprintf("-%d", limit), "--pretty=format:%H|%an|%at|%s", "--name-only")
	if err != nil {
		return nil, err
	}

	commits := []*GitCommit{}
	lines := strings.Split(output, "\n")
	
	var currentCommit *GitCommit
	for _, line := range lines {
		if line == "" {
			if currentCommit != nil {
				commits = append(commits, currentCommit)
				currentCommit = nil
			}
			continue
		}
		
		if strings.Contains(line, "|") {
			parts := strings.Split(line, "|")
			if len(parts) >= 4 {
				timestamp, _ := strconv.ParseInt(parts[2], 10, 64)
				currentCommit = &GitCommit{
					Hash:    parts[0],
					Author:  parts[1],
					Date:    time.Unix(timestamp, 0),
					Message: parts[3],
					Files:   []string{},
				}
			}
		} else if currentCommit != nil {
			currentCommit.Files = append(currentCommit.Files, line)
		}
	}
	
	if currentCommit != nil {
		commits = append(commits, currentCommit)
	}
	
	return commits, nil
}
