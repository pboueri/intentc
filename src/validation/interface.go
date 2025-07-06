package validation

import (
	"context"
	"fmt"
	"time"
	
	"github.com/pboueri/intentc/src"
)

type Validator interface {
	Validate(ctx context.Context, validation *src.Validation, projectPath string) (*ValidationResult, error)
	GetType() src.ValidationType
}

type ValidationResult struct {
	Passed      bool
	Message     string
	Details     []string
	ValidatedAt time.Time
}

type ValidatorRegistry struct {
	validators map[src.ValidationType]Validator
}

func NewValidatorRegistry() *ValidatorRegistry {
	return &ValidatorRegistry{
		validators: make(map[src.ValidationType]Validator),
	}
}

func (r *ValidatorRegistry) RegisterValidator(validationType src.ValidationType, validator Validator) {
	r.validators[validationType] = validator
}

func (r *ValidatorRegistry) GetValidator(validationType src.ValidationType) (Validator, error) {
	validator, exists := r.validators[validationType]
	if !exists {
		return nil, fmt.Errorf("unknown validation type: %s", validationType)
	}
	return validator, nil
}

func (r *ValidatorRegistry) RunValidation(ctx context.Context, validation *src.Validation, projectPath string) (*ValidationResult, error) {
	validator, err := r.GetValidator(validation.Type)
	if err != nil {
		return nil, err
	}
	return validator.Validate(ctx, validation, projectPath)
}
