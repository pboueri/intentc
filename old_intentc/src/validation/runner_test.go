package validation

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/pboueri/intentc/src"
)

type mockValidator struct {
	shouldPass bool
	message    string
	delay      time.Duration
}

func (m *mockValidator) Validate(ctx context.Context, validation *src.Validation, projectPath string) (*ValidationResult, error) {
	if m.delay > 0 {
		time.Sleep(m.delay)
	}
	
	return &ValidationResult{
		Passed:      m.shouldPass,
		Message:     m.message,
		ValidatedAt: time.Now(),
	}, nil
}

func (m *mockValidator) GetType() src.ValidationType {
	return "mock"
}

func TestRunner_RunTargetValidations(t *testing.T) {
	registry := NewValidatorRegistry()
	registry.RegisterValidator("mock", &mockValidator{shouldPass: true, message: "mock passed"})
	
	runner := NewRunner("/tmp", registry)

	target := &src.Target{
		Name: "test-target",
		Validations: []*src.ValidationFile{
			{
				Validations: []src.Validation{
					{
						Name:        "test1",
						Type:        "mock",
						Description: "Test validation 1",
						Parameters:  map[string]interface{}{},
					},
					{
						Name:        "test2",
						Type:        "mock",
						Description: "Test validation 2",
						Parameters:  map[string]interface{}{},
					},
				},
			},
		},
	}

	t.Run("sequential execution", func(t *testing.T) {
		result, err := runner.RunTargetValidations(context.Background(), target, RunOptions{
			Parallel: false,
		})

		if err != nil {
			t.Fatalf("RunTargetValidations failed: %v", err)
		}

		if result.TotalValidations != 2 {
			t.Errorf("Expected 2 total validations, got %d", result.TotalValidations)
		}

		if result.Passed != 2 {
			t.Errorf("Expected 2 passed validations, got %d", result.Passed)
		}

		if result.Failed != 0 {
			t.Errorf("Expected 0 failed validations, got %d", result.Failed)
		}
	})

	t.Run("parallel execution", func(t *testing.T) {
		result, err := runner.RunTargetValidations(context.Background(), target, RunOptions{
			Parallel: true,
		})

		if err != nil {
			t.Fatalf("RunTargetValidations failed: %v", err)
		}

		if result.TotalValidations != 2 {
			t.Errorf("Expected 2 total validations, got %d", result.TotalValidations)
		}

		if result.Passed != 2 {
			t.Errorf("Expected 2 passed validations, got %d", result.Passed)
		}
	})

	t.Run("with failures", func(t *testing.T) {
		registry.RegisterValidator("mock-fail", &mockValidator{shouldPass: false, message: "mock failed"})
		
		targetWithFailure := &src.Target{
			Name: "test-target",
			Validations: []*src.ValidationFile{
				{
					Validations: []src.Validation{
						{
							Name: "test-pass",
							Type: "mock",
						},
						{
							Name: "test-fail",
							Type: "mock-fail",
						},
					},
				},
			},
		}

		result, err := runner.RunTargetValidations(context.Background(), targetWithFailure, RunOptions{})
		if err != nil {
			t.Fatalf("RunTargetValidations failed: %v", err)
		}

		if result.Passed != 1 {
			t.Errorf("Expected 1 passed validation, got %d", result.Passed)
		}

		if result.Failed != 1 {
			t.Errorf("Expected 1 failed validation, got %d", result.Failed)
		}
	})

	t.Run("hidden validations", func(t *testing.T) {
		targetWithHidden := &src.Target{
			Name: "test-target",
			Validations: []*src.ValidationFile{
				{
					Validations: []src.Validation{
						{
							Name: "visible",
							Type: "mock",
						},
						{
							Name:   "hidden",
							Type:   "mock",
							Hidden: true,
						},
					},
				},
			},
		}

		result, err := runner.RunTargetValidations(context.Background(), targetWithHidden, RunOptions{})
		if err != nil {
			t.Fatalf("RunTargetValidations failed: %v", err)
		}

		// Hidden validations should not be counted or run
		if result.TotalValidations != 2 {
			t.Errorf("Expected 2 total validations (including hidden), got %d", result.TotalValidations)
		}

		if result.Passed != 1 {
			t.Errorf("Expected 1 passed validation (hidden excluded), got %d", result.Passed)
		}
	})
}

func TestRunner_GenerateReport(t *testing.T) {
	runner := NewRunner("/tmp", NewValidatorRegistry())

	result := &RunResult{
		Target:           "test-target",
		TotalValidations: 3,
		Passed:           2,
		Failed:           1,
		Results: map[string]*ValidationResult{
			"test1": {
				Passed:  true,
				Message: "File exists",
				Details: []string{"Path: /tmp/test.txt"},
			},
			"test2": {
				Passed:  false,
				Message: "File not found",
				Details: []string{"Path: /tmp/missing.txt"},
			},
		},
		Errors: map[string]error{
			"test3": fmt.Errorf("validation error"),
		},
	}

	report := runner.GenerateReport(result)

	if !contains(report, "test-target") {
		t.Error("Report should contain target name")
	}

	if !contains(report, "Total: 3") {
		t.Error("Report should contain total count")
	}

	if !contains(report, "Passed: 2") {
		t.Error("Report should contain passed count")
	}

	if !contains(report, "Failed: 1") {
		t.Error("Report should contain failed count")
	}

	if !contains(report, "[PASS] test1") {
		t.Error("Report should contain passed test")
	}

	if !contains(report, "[FAIL] test2") {
		t.Error("Report should contain failed test")
	}

	if !contains(report, "[ERROR] test3") {
		t.Error("Report should contain error test")
	}
}