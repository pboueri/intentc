package parser

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestIntentParser_ParseIntent(t *testing.T) {
	tests := []struct {
		name     string
		content  string
		expected struct {
			name         string
			dependencies []string
		}
	}{
		{
			name: "intent with dependencies",
			content: `# Test Feature

## Dependencies

Depends On: feature1, feature2

## Intent

This is a test feature.`,
			expected: struct {
				name         string
				dependencies []string
			}{
				name:         "test_feature",
				dependencies: []string{"feature1", "feature2"},
			},
		},
		{
			name: "intent without dependencies",
			content: `# Simple Feature

## Intent

This is a simple feature.`,
			expected: struct {
				name         string
				dependencies []string
			}{
				name:         "test_feature",
				dependencies: []string{},
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tmpDir, err := os.MkdirTemp("", "intent-parser-test")
			require.NoError(t, err)
			defer os.RemoveAll(tmpDir)

			featureDir := filepath.Join(tmpDir, "test_feature")
			err = os.MkdirAll(featureDir, 0755)
			require.NoError(t, err)

			intentFile := filepath.Join(featureDir, "feature.ic")
			err = os.WriteFile(intentFile, []byte(tt.content), 0644)
			require.NoError(t, err)

			parser := NewIntentParser()
			intent, err := parser.ParseIntent(intentFile)
			require.NoError(t, err)

			assert.Equal(t, tt.expected.name, intent.Name)
			assert.Equal(t, tt.expected.dependencies, intent.Dependencies)
			assert.Equal(t, tt.content, intent.Content)
			assert.Equal(t, intentFile, intent.FilePath)
		})
	}
}

func TestIntentParser_FindIntentFiles(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "intent-find-test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	intentDir := filepath.Join(tmpDir, "intent")
	err = os.MkdirAll(intentDir, 0755)
	require.NoError(t, err)

	projectIC := filepath.Join(intentDir, "project.ic")
	err = os.WriteFile(projectIC, []byte("# Project"), 0644)
	require.NoError(t, err)

	feature1Dir := filepath.Join(intentDir, "feature1")
	err = os.MkdirAll(feature1Dir, 0755)
	require.NoError(t, err)

	feature1IC := filepath.Join(feature1Dir, "feature.ic")
	err = os.WriteFile(feature1IC, []byte("# Feature 1"), 0644)
	require.NoError(t, err)

	feature2Dir := filepath.Join(intentDir, "feature2")
	err = os.MkdirAll(feature2Dir, 0755)
	require.NoError(t, err)

	feature2IC := filepath.Join(feature2Dir, "feature.ic")
	err = os.WriteFile(feature2IC, []byte("# Feature 2"), 0644)
	require.NoError(t, err)

	notIC := filepath.Join(intentDir, "readme.md")
	err = os.WriteFile(notIC, []byte("Not an intent file"), 0644)
	require.NoError(t, err)

	parser := NewIntentParser()
	files, err := parser.FindIntentFiles(intentDir)
	require.NoError(t, err)

	assert.Len(t, files, 3)
	assert.Contains(t, files, projectIC)
	assert.Contains(t, files, feature1IC)
	assert.Contains(t, files, feature2IC)
}
