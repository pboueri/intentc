package state

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/git"
)

type GitStateManager struct {
	git         git.GitManager
	projectRoot string
	stateDir    string
}

func NewGitStateManager(gitInterface git.GitManager, projectRoot string) *GitStateManager {
	return &GitStateManager{
		git:         gitInterface,
		projectRoot: projectRoot,
		stateDir:    filepath.Join(projectRoot, ".intentc", "state"),
	}
}

func (m *GitStateManager) Initialize(ctx context.Context) error {
	// Ensure .intentc exists as a directory
	intentcPath := filepath.Join(m.projectRoot, ".intentc")
	if info, err := os.Stat(intentcPath); err == nil && !info.IsDir() {
		// If .intentc exists but is not a directory, we have a problem
		return fmt.Errorf(".intentc exists but is not a directory - please run 'intentc init' to fix this")
	}
	
	// Create state directory
	if err := os.MkdirAll(m.stateDir, 0755); err != nil {
		return fmt.Errorf("failed to create state directory: %w", err)
	}

	statusFile := filepath.Join(m.stateDir, "status.json")
	if _, err := os.Stat(statusFile); os.IsNotExist(err) {
		status := make(map[string]src.TargetStatus)
		if err := m.writeJSON(statusFile, status); err != nil {
			return fmt.Errorf("failed to initialize status file: %w", err)
		}
	}

	return nil
}

func (m *GitStateManager) SaveBuildResult(ctx context.Context, result *src.BuildResult) error {
	targetDir := filepath.Join(m.stateDir, "builds", result.Target)
	if err := os.MkdirAll(targetDir, 0755); err != nil {
		return fmt.Errorf("failed to create target directory: %w", err)
	}

	resultFile := filepath.Join(targetDir, fmt.Sprintf("%s.json", result.GenerationID))
	if err := m.writeJSON(resultFile, result); err != nil {
		return fmt.Errorf("failed to save build result: %w", err)
	}

	latestLink := filepath.Join(targetDir, "latest.json")
	if err := m.writeJSON(latestLink, result); err != nil {
		return fmt.Errorf("failed to update latest link: %w", err)
	}

	return nil
}

func (m *GitStateManager) GetBuildResult(ctx context.Context, target string, generationID string) (*src.BuildResult, error) {
	resultFile := filepath.Join(m.stateDir, "builds", target, fmt.Sprintf("%s.json", generationID))
	
	var result src.BuildResult
	if err := m.readJSON(resultFile, &result); err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("build result not found for target %s with generation ID %s", target, generationID)
		}
		return nil, fmt.Errorf("failed to read build result: %w", err)
	}

	return &result, nil
}

func (m *GitStateManager) GetLatestBuildResult(ctx context.Context, target string) (*src.BuildResult, error) {
	latestFile := filepath.Join(m.stateDir, "builds", target, "latest.json")
	
	var result src.BuildResult
	if err := m.readJSON(latestFile, &result); err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("failed to read latest build result: %w", err)
	}

	return &result, nil
}

func (m *GitStateManager) ListBuildResults(ctx context.Context, target string) ([]*src.BuildResult, error) {
	targetDir := filepath.Join(m.stateDir, "builds", target)
	
	entries, err := os.ReadDir(targetDir)
	if err != nil {
		if os.IsNotExist(err) {
			return []*src.BuildResult{}, nil
		}
		return nil, fmt.Errorf("failed to read target directory: %w", err)
	}

	var results []*src.BuildResult
	for _, entry := range entries {
		if entry.IsDir() || entry.Name() == "latest.json" {
			continue
		}

		var result src.BuildResult
		resultFile := filepath.Join(targetDir, entry.Name())
		if err := m.readJSON(resultFile, &result); err != nil {
			continue
		}
		results = append(results, &result)
	}

	return results, nil
}

func (m *GitStateManager) CommitChanges(ctx context.Context, message string, files []string) error {
	if err := m.git.Add(ctx, files); err != nil {
		return fmt.Errorf("failed to add files: %w", err)
	}

	if err := m.git.Commit(ctx, message); err != nil {
		return fmt.Errorf("failed to commit changes: %w", err)
	}

	return nil
}

func (m *GitStateManager) GetTargetStatus(ctx context.Context, target string) (src.TargetStatus, error) {
	statusFile := filepath.Join(m.stateDir, "status.json")
	
	status := make(map[string]src.TargetStatus)
	if err := m.readJSON(statusFile, &status); err != nil {
		if os.IsNotExist(err) {
			return src.TargetStatusPending, nil
		}
		return "", fmt.Errorf("failed to read status file: %w", err)
	}

	if s, ok := status[target]; ok {
		return s, nil
	}
	return src.TargetStatusPending, nil
}

func (m *GitStateManager) UpdateTargetStatus(ctx context.Context, target string, status src.TargetStatus) error {
	statusFile := filepath.Join(m.stateDir, "status.json")
	
	statusMap := make(map[string]src.TargetStatus)
	if err := m.readJSON(statusFile, &statusMap); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("failed to read status file: %w", err)
	}

	statusMap[target] = status
	
	if err := m.writeJSON(statusFile, statusMap); err != nil {
		return fmt.Errorf("failed to write status file: %w", err)
	}

	return nil
}

func (m *GitStateManager) writeJSON(filename string, data interface{}) error {
	file, err := os.Create(filename)
	if err != nil {
		return err
	}
	defer file.Close()

	encoder := json.NewEncoder(file)
	encoder.SetIndent("", "  ")
	return encoder.Encode(data)
}

func (m *GitStateManager) readJSON(filename string, data interface{}) error {
	file, err := os.Open(filename)
	if err != nil {
		return err
	}
	defer file.Close()

	return json.NewDecoder(file).Decode(data)
}