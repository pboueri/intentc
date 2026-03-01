package validation

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/pboueri/intentc/src"
)

func TestFileCheckValidator_Validate(t *testing.T) {
	tmpDir := t.TempDir()
	validator := NewFileCheckValidator()

	t.Run("file exists check - file present", func(t *testing.T) {
		// Create test file
		testFile := filepath.Join(tmpDir, "test.txt")
		os.WriteFile(testFile, []byte("test content"), 0644)

		validation := &src.Validation{
			Name: "test file exists",
			Type: src.ValidationTypeFileCheck,
			Parameters: map[string]interface{}{
				"file":   "test.txt",
				"exists": true,
			},
		}

		result, err := validator.Validate(context.Background(), validation, tmpDir)
		if err != nil {
			t.Fatalf("Validate failed: %v", err)
		}

		if !result.Passed {
			t.Errorf("Expected validation to pass, but it failed: %s", result.Message)
		}
	})

	t.Run("file exists check - file missing", func(t *testing.T) {
		validation := &src.Validation{
			Name: "test file missing",
			Type: src.ValidationTypeFileCheck,
			Parameters: map[string]interface{}{
				"file":   "missing.txt",
				"exists": false,
			},
		}

		result, err := validator.Validate(context.Background(), validation, tmpDir)
		if err != nil {
			t.Fatalf("Validate failed: %v", err)
		}

		if !result.Passed {
			t.Errorf("Expected validation to pass, but it failed: %s", result.Message)
		}
	})

	t.Run("file contains check", func(t *testing.T) {
		// Create test file with specific content
		testFile := filepath.Join(tmpDir, "contains.txt")
		os.WriteFile(testFile, []byte("Hello World\nThis is a test"), 0644)

		validation := &src.Validation{
			Name: "test file contains",
			Type: src.ValidationTypeFileCheck,
			Parameters: map[string]interface{}{
				"file":     "contains.txt",
				"exists":   true,
				"contains": "Hello World",
			},
		}

		result, err := validator.Validate(context.Background(), validation, tmpDir)
		if err != nil {
			t.Fatalf("Validate failed: %v", err)
		}

		if !result.Passed {
			t.Errorf("Expected validation to pass, but it failed: %s", result.Message)
		}
	})

	t.Run("file contains check - text not found", func(t *testing.T) {
		// Create test file with specific content
		testFile := filepath.Join(tmpDir, "contains2.txt")
		os.WriteFile(testFile, []byte("Hello World"), 0644)

		validation := &src.Validation{
			Name: "test file contains missing text",
			Type: src.ValidationTypeFileCheck,
			Parameters: map[string]interface{}{
				"file":     "contains2.txt",
				"exists":   true,
				"contains": "Goodbye",
			},
		}

		result, err := validator.Validate(context.Background(), validation, tmpDir)
		if err != nil {
			t.Fatalf("Validate failed: %v", err)
		}

		if result.Passed {
			t.Errorf("Expected validation to fail, but it passed")
		}
	})

	t.Run("missing required parameter", func(t *testing.T) {
		validation := &src.Validation{
			Name:       "test missing param",
			Type:       src.ValidationTypeFileCheck,
			Parameters: map[string]interface{}{},
		}

		_, err := validator.Validate(context.Background(), validation, tmpDir)
		if err == nil {
			t.Error("Expected error for missing 'file' parameter")
		}
	})
}