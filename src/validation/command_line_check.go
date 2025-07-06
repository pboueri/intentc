package validation

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"strings"
	"time"

	"github.com/pboueri/intentc/src"
)

type CommandLineCheckValidator struct{}

func NewCommandLineCheckValidator() *CommandLineCheckValidator {
	return &CommandLineCheckValidator{}
}

func (v *CommandLineCheckValidator) GetType() src.ValidationType {
	return src.ValidationTypeCommandLineCheck
}

func (v *CommandLineCheckValidator) Validate(ctx context.Context, validation *src.Validation, projectPath string) (*ValidationResult, error) {
	result := &ValidationResult{
		ValidatedAt: time.Now(),
		Details:     []string{},
	}

	// Extract command parameter
	commandParam, ok := validation.Parameters["command"]
	if !ok {
		return nil, fmt.Errorf("missing required parameter 'command'")
	}

	command, ok := commandParam.(string)
	if !ok {
		return nil, fmt.Errorf("parameter 'command' must be a string")
	}

	// Parse command
	parts := strings.Fields(command)
	if len(parts) == 0 {
		return nil, fmt.Errorf("empty command")
	}

	// Create command
	cmd := exec.CommandContext(ctx, parts[0], parts[1:]...)
	cmd.Dir = projectPath

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	// Run command
	err := cmd.Run()
	result.Details = append(result.Details, fmt.Sprintf("Command: %s", command))
	result.Details = append(result.Details, fmt.Sprintf("Working directory: %s", projectPath))

	if stdout.Len() > 0 {
		result.Details = append(result.Details, fmt.Sprintf("Stdout: %s", strings.TrimSpace(stdout.String())))
	}
	if stderr.Len() > 0 {
		result.Details = append(result.Details, fmt.Sprintf("Stderr: %s", strings.TrimSpace(stderr.String())))
	}

	// Check exit code
	expectedExitCode := 0
	if exitCodeParam, hasExitCode := validation.Parameters["exit_code"]; hasExitCode {
		exitCode, ok := exitCodeParam.(float64) // JSON numbers are float64
		if !ok {
			return nil, fmt.Errorf("parameter 'exit_code' must be a number")
		}
		expectedExitCode = int(exitCode)
	}

	actualExitCode := 0
	if err != nil {
		if exitError, ok := err.(*exec.ExitError); ok {
			actualExitCode = exitError.ExitCode()
		} else {
			result.Passed = false
			result.Message = fmt.Sprintf("Command failed to execute: %v", err)
			return result, nil
		}
	}

	if actualExitCode == expectedExitCode {
		result.Details = append(result.Details, fmt.Sprintf("Exit code: %d (expected)", actualExitCode))
	} else {
		result.Passed = false
		result.Message = fmt.Sprintf("Command exited with code %d, expected %d", actualExitCode, expectedExitCode)
		return result, nil
	}

	// Check output contains
	if outputContainsParam, hasOutputContains := validation.Parameters["output_contains"]; hasOutputContains {
		containsStr, ok := outputContainsParam.(string)
		if !ok {
			return nil, fmt.Errorf("parameter 'output_contains' must be a string")
		}

		output := stdout.String()
		if strings.Contains(output, containsStr) {
			result.Details = append(result.Details, fmt.Sprintf("Output contains expected text: %s", containsStr))
		} else {
			result.Passed = false
			result.Message = fmt.Sprintf("Command output does not contain expected text: %s", containsStr)
			return result, nil
		}
	}

	// Check error contains
	if errorContainsParam, hasErrorContains := validation.Parameters["error_contains"]; hasErrorContains {
		containsStr, ok := errorContainsParam.(string)
		if !ok {
			return nil, fmt.Errorf("parameter 'error_contains' must be a string")
		}

		errorOutput := stderr.String()
		if strings.Contains(errorOutput, containsStr) {
			result.Details = append(result.Details, fmt.Sprintf("Error output contains expected text: %s", containsStr))
		} else {
			result.Passed = false
			result.Message = fmt.Sprintf("Command error output does not contain expected text: %s", containsStr)
			return result, nil
		}
	}

	// If we got here, all checks passed
	result.Passed = true
	result.Message = fmt.Sprintf("Command '%s' completed successfully", command)

	return result, nil
}