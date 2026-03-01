package validation

import (
	"context"
	"fmt"
	"time"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/agent"
)

type ProjectCheckValidator struct {
	agent agent.Agent
}

func NewProjectCheckValidator(agent agent.Agent) *ProjectCheckValidator {
	return &ProjectCheckValidator{
		agent: agent,
	}
}

func (v *ProjectCheckValidator) GetType() src.ValidationType {
	return src.ValidationTypeProjectCheck
}

func (v *ProjectCheckValidator) Validate(ctx context.Context, validation *src.Validation, projectPath string) (*ValidationResult, error) {
	result := &ValidationResult{
		ValidatedAt: time.Now(),
		Details:     []string{},
	}

	// Extract the check description
	checkParam, ok := validation.Parameters["check"]
	if !ok {
		return nil, fmt.Errorf("missing required parameter 'check'")
	}

	checkDesc, ok := checkParam.(string)
	if !ok {
		return nil, fmt.Errorf("parameter 'check' must be a string")
	}

	// Use the agent to validate the project-level check
	// For now, we'll use a simple implementation
	// In a real implementation, this would use the agent's validation capabilities
	passed, message, err := v.agent.Validate(ctx, validation, []string{projectPath})
	if err != nil {
		return nil, fmt.Errorf("agent validation failed: %w", err)
	}

	result.Passed = passed
	result.Message = message
	result.Details = append(result.Details, fmt.Sprintf("Check performed: %s", checkDesc))

	return result, nil
}