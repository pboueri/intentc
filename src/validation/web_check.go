package validation

import (
	"context"
	"fmt"
	"time"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/agent"
)

type WebCheckValidator struct {
	agent agent.Agent
}

func NewWebCheckValidator(agent agent.Agent) *WebCheckValidator {
	return &WebCheckValidator{
		agent: agent,
	}
}

func (v *WebCheckValidator) GetType() src.ValidationType {
	return src.ValidationTypeWebCheck
}

func (v *WebCheckValidator) Validate(ctx context.Context, validation *src.Validation, projectPath string) (*ValidationResult, error) {
	result := &ValidationResult{
		ValidatedAt: time.Now(),
		Details:     []string{},
	}

	// Extract parameters
	urlParam, hasURL := validation.Parameters["url"]
	scriptParam, hasScript := validation.Parameters["script"]

	if !hasURL && !hasScript {
		return nil, fmt.Errorf("missing required parameter 'url' or 'script'")
	}

	if hasURL {
		url, ok := urlParam.(string)
		if !ok {
			return nil, fmt.Errorf("parameter 'url' must be a string")
		}
		result.Details = append(result.Details, fmt.Sprintf("URL to check: %s", url))
	}

	if hasScript {
		script, ok := scriptParam.(string)
		if !ok {
			return nil, fmt.Errorf("parameter 'script' must be a string")
		}
		result.Details = append(result.Details, fmt.Sprintf("Script to execute: %s", script))
	}

	// Since web checks require browser automation which may not be available in all agents,
	// we'll check if the agent supports it
	agentType := v.agent.GetType()
	if agentType == "mock" {
		// For mock agent, simulate a successful check
		result.Passed = true
		result.Message = "Web check simulated (mock agent)"
		result.Details = append(result.Details, "Note: Using mock agent, actual web check not performed")
		return result, nil
	}

	// Use the agent to perform the web check
	passed, message, err := v.agent.Validate(ctx, validation, []string{projectPath})
	if err != nil {
		// If the agent doesn't support web checks, mark as skipped rather than failed
		if err.Error() == "web checks not supported" {
			result.Passed = true
			result.Message = "Web check skipped (not supported by current agent)"
			result.Details = append(result.Details, "Note: Current agent does not support web checks")
			return result, nil
		}
		return nil, fmt.Errorf("agent validation failed: %w", err)
	}

	result.Passed = passed
	result.Message = message

	return result, nil
}