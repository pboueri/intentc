package builder

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/pboueri/intentc/src/agent"
	"github.com/pboueri/intentc/src/config"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestBuildDirectoryIntegration tests the complete build directory isolation feature
func TestBuildDirectoryIntegration(t *testing.T) {
	// Create test project
	projectRoot := setupTestProject(t)
	
	// Create mock agent that generates files
	mockAgent := agent.NewMockAgent("test-agent")
	mockAgent.BuildFunc = func(ctx context.Context, buildCtx agent.BuildContext) ([]string, error) {
		// Create files in the current working directory (should be build directory)
		files := []string{
			filepath.Join("src", buildCtx.Intent.Name+".go"),
			filepath.Join("tests", buildCtx.Intent.Name+"_test.go"),
			"README.md",
		}
		
		for _, file := range files {
			fullPath := filepath.Join(buildCtx.BuildPath, file)
			os.MkdirAll(filepath.Dir(fullPath), 0755)
			content := "// Generated for " + buildCtx.BuildName
			os.WriteFile(fullPath, []byte(content), 0644)
		}
		
		return files, nil
	}
	
	// Create state manager
	mockState := newMockStateManager()
	
	// Create config with custom build names
	cfg := &config.Config{
		Build: config.BuildConfig{
			DefaultBuildName: "main",
		},
	}
	
	// Create builder
	builder := NewBuilder(projectRoot, mockAgent, mockState, &mockGitManager{}, cfg)
	
	// Test 1: Build with default directory
	err := builder.Build(context.Background(), BuildOptions{
		Target: "feature1",
	})
	require.NoError(t, err)
	
	// Verify files exist in default build directory
	defaultBuildPath := filepath.Join(projectRoot, "build-main")
	assert.DirExists(t, defaultBuildPath)
	assert.FileExists(t, filepath.Join(defaultBuildPath, "src", "feature1.go"))
	assert.FileExists(t, filepath.Join(defaultBuildPath, "tests", "feature1_test.go"))
	assert.FileExists(t, filepath.Join(defaultBuildPath, "README.md"))
	
	// Test 2: Build with custom directory
	err = builder.Build(context.Background(), BuildOptions{
		Target:    "feature2",
		BuildName: "experimental",
	})
	require.NoError(t, err)
	
	// Verify files exist in experimental build directory
	expBuildPath := filepath.Join(projectRoot, "build-experimental")
	assert.DirExists(t, expBuildPath)
	assert.FileExists(t, filepath.Join(expBuildPath, "src", "feature2.go"))
	assert.FileExists(t, filepath.Join(expBuildPath, "tests", "feature2_test.go"))
	assert.FileExists(t, filepath.Join(expBuildPath, "README.md"))
	
	// Test 3: Verify isolation - files don't exist in project root
	assert.NoFileExists(t, filepath.Join(projectRoot, "src", "feature1.go"))
	assert.NoFileExists(t, filepath.Join(projectRoot, "src", "feature2.go"))
	assert.NoFileExists(t, filepath.Join(projectRoot, "README.md"))
	
	// Test 4: Verify isolation between builds
	// Note: With per-build state tracking, feature1 can exist in both builds
	// Check that feature2 files don't exist in main build (it was only built in experimental)
	assert.NoFileExists(t, filepath.Join(defaultBuildPath, "src", "feature2.go"))
	
	// Test 5: Verify build results include build info
	result1, err := mockState.GetLatestBuildResultForBuild(context.Background(), "feature1", "main")
	require.NoError(t, err)
	assert.Equal(t, "main", result1.BuildName)
	assert.Equal(t, defaultBuildPath, result1.BuildPath)
	
	result2, err := mockState.GetLatestBuildResultForBuild(context.Background(), "feature2", "experimental")
	require.NoError(t, err)
	assert.Equal(t, "experimental", result2.BuildName)
	assert.Equal(t, expBuildPath, result2.BuildPath)
}