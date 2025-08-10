package state

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/pboueri/intentc/src"
)

// NoOpStateManager is a simple file-based state manager for when git is not available
type NoOpStateManager struct {
	projectRoot string
	stateDir    string
}

// NewNoOpStateManager creates a new no-op state manager
func NewNoOpStateManager(projectRoot string) *NoOpStateManager {
	return &NoOpStateManager{
		projectRoot: projectRoot,
		stateDir:    filepath.Join(projectRoot, ".intentc", "state"),
	}
}

func (m *NoOpStateManager) Initialize(ctx context.Context) error {
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

func (m *NoOpStateManager) SaveBuildResult(ctx context.Context, result *src.BuildResult) error {
	// Save in the global location
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

	// Also save in build-specific location if BuildName is set
	if result.BuildName != "" {
		buildTargetDir := filepath.Join(m.stateDir, "builds", result.BuildName, result.Target)
		if err := os.MkdirAll(buildTargetDir, 0755); err != nil {
			return fmt.Errorf("failed to create build-specific target directory: %w", err)
		}
		
		buildResultFile := filepath.Join(buildTargetDir, fmt.Sprintf("%s.json", result.GenerationID))
		if err := m.writeJSON(buildResultFile, result); err != nil {
			return fmt.Errorf("failed to save build-specific result: %w", err)
		}
		
		buildLatestLink := filepath.Join(buildTargetDir, "latest.json")
		if err := m.writeJSON(buildLatestLink, result); err != nil {
			return fmt.Errorf("failed to update build-specific latest link: %w", err)
		}
	}

	return nil
}

func (m *NoOpStateManager) GetBuildResult(ctx context.Context, target string, generationID string) (*src.BuildResult, error) {
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

func (m *NoOpStateManager) GetLatestBuildResult(ctx context.Context, target string) (*src.BuildResult, error) {
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

func (m *NoOpStateManager) ListBuildResults(ctx context.Context, target string) ([]*src.BuildResult, error) {
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

func (m *NoOpStateManager) CommitChanges(ctx context.Context, message string, files []string) error {
	// No-op: silently succeed since we don't have git
	return nil
}

func (m *NoOpStateManager) GetTargetStatus(ctx context.Context, target string) (src.TargetStatus, error) {
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

func (m *NoOpStateManager) UpdateTargetStatus(ctx context.Context, target string, status src.TargetStatus) error {
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

func (m *NoOpStateManager) GetTargetStatusForBuild(ctx context.Context, target string, buildName string) (src.TargetStatus, error) {
	statusFile := filepath.Join(m.stateDir, "builds", buildName, "status.json")
	
	statuses := make(map[string]src.TargetStatus)
	if err := m.readJSON(statusFile, &statuses); err != nil {
		if os.IsNotExist(err) {
			return src.TargetStatusPending, nil
		}
		return src.TargetStatusPending, fmt.Errorf("failed to read build status: %w", err)
	}
	
	status, exists := statuses[target]
	if !exists {
		return src.TargetStatusPending, nil
	}
	
	return status, nil
}

func (m *NoOpStateManager) UpdateTargetStatusForBuild(ctx context.Context, target string, buildName string, status src.TargetStatus) error {
	buildDir := filepath.Join(m.stateDir, "builds", buildName)
	if err := os.MkdirAll(buildDir, 0755); err != nil {
		return fmt.Errorf("failed to create build directory: %w", err)
	}
	
	statusFile := filepath.Join(buildDir, "status.json")
	
	statuses := make(map[string]src.TargetStatus)
	if err := m.readJSON(statusFile, &statuses); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("failed to read existing status: %w", err)
	}
	
	statuses[target] = status
	
	if err := m.writeJSON(statusFile, statuses); err != nil {
		return fmt.Errorf("failed to write status: %w", err)
	}
	
	return nil
}

func (m *NoOpStateManager) GetLatestBuildResultForBuild(ctx context.Context, target string, buildName string) (*src.BuildResult, error) {
	latestFile := filepath.Join(m.stateDir, "builds", buildName, target, "latest.json")
	
	var result src.BuildResult
	if err := m.readJSON(latestFile, &result); err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("failed to read latest build result: %w", err)
	}
	
	return &result, nil
}

func (m *NoOpStateManager) writeJSON(filename string, data interface{}) error {
	// Ensure directory exists
	if err := os.MkdirAll(filepath.Dir(filename), 0755); err != nil {
		return err
	}

	file, err := os.Create(filename)
	if err != nil {
		return err
	}
	defer file.Close()

	encoder := json.NewEncoder(file)
	encoder.SetIndent("", "  ")
	return encoder.Encode(data)
}

func (m *NoOpStateManager) readJSON(filename string, data interface{}) error {
	file, err := os.Open(filename)
	if err != nil {
		return err
	}
	defer file.Close()

	return json.NewDecoder(file).Decode(data)
}