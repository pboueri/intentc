package validation

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/pboueri/intentc/src"
)

type Runner struct {
	registry    *ValidatorRegistry
	projectRoot string
}

type RunOptions struct {
	Parallel bool
	Timeout  time.Duration
}

type RunResult struct {
	Target          string
	TotalValidations int
	Passed          int
	Failed          int
	Results         map[string]*ValidationResult
	Errors          map[string]error
}

func NewRunner(projectRoot string, registry *ValidatorRegistry) *Runner {
	return &Runner{
		registry:    registry,
		projectRoot: projectRoot,
	}
}

func (r *Runner) RunTargetValidations(ctx context.Context, target *src.Target, opts RunOptions) (*RunResult, error) {
	result := &RunResult{
		Target:  target.Name,
		Results: make(map[string]*ValidationResult),
		Errors:  make(map[string]error),
	}

	// Collect all validations from all validation files
	var allValidations []*src.Validation
	for _, valFile := range target.Validations {
		for i := range valFile.Validations {
			allValidations = append(allValidations, &valFile.Validations[i])
		}
	}

	result.TotalValidations = len(allValidations)

	if opts.Parallel {
		r.runParallel(ctx, allValidations, result, opts)
	} else {
		r.runSequential(ctx, allValidations, result, opts)
	}

	return result, nil
}

func (r *Runner) runSequential(ctx context.Context, validations []*src.Validation, result *RunResult, opts RunOptions) {
	for _, validation := range validations {
		if validation.Hidden {
			continue
		}

		valCtx := ctx
		if opts.Timeout > 0 {
			var cancel context.CancelFunc
			valCtx, cancel = context.WithTimeout(ctx, opts.Timeout)
			defer cancel()
		}

		valResult, err := r.registry.RunValidation(valCtx, validation, r.projectRoot)
		if err != nil {
			result.Errors[validation.Name] = err
			result.Failed++
		} else {
			result.Results[validation.Name] = valResult
			if valResult.Passed {
				result.Passed++
			} else {
				result.Failed++
			}
		}
	}
}

func (r *Runner) runParallel(ctx context.Context, validations []*src.Validation, result *RunResult, opts RunOptions) {
	var wg sync.WaitGroup
	var mu sync.Mutex

	for _, validation := range validations {
		if validation.Hidden {
			continue
		}

		wg.Add(1)
		go func(val *src.Validation) {
			defer wg.Done()

			valCtx := ctx
			if opts.Timeout > 0 {
				var cancel context.CancelFunc
				valCtx, cancel = context.WithTimeout(ctx, opts.Timeout)
				defer cancel()
			}

			valResult, err := r.registry.RunValidation(valCtx, val, r.projectRoot)
			
			mu.Lock()
			defer mu.Unlock()
			
			if err != nil {
				result.Errors[val.Name] = err
				result.Failed++
			} else {
				result.Results[val.Name] = valResult
				if valResult.Passed {
					result.Passed++
				} else {
					result.Failed++
				}
			}
		}(validation)
	}

	wg.Wait()
}

func (r *Runner) GenerateReport(result *RunResult) string {
	report := fmt.Sprintf("Validation Report for %s\n", result.Target)
	report += fmt.Sprintf("Total: %d | Passed: %d | Failed: %d\n\n", 
		result.TotalValidations, result.Passed, result.Failed)

	if len(result.Results) > 0 {
		report += "Results:\n"
		for name, valResult := range result.Results {
			if valResult == nil {
				report += fmt.Sprintf("  [ERROR] %s: validation result is nil\n", name)
				continue
			}
			status := "PASS"
			if !valResult.Passed {
				status = "FAIL"
			}
			msg := valResult.Message
			if msg == "" {
				msg = "(no message)"
			}
			report += fmt.Sprintf("  [%s] %s: %s\n", status, name, msg)
			
			if len(valResult.Details) > 0 {
				for _, detail := range valResult.Details {
					report += fmt.Sprintf("    - %s\n", detail)
				}
			}
		}
	}

	if len(result.Errors) > 0 {
		report += "\nErrors:\n"
		for name, err := range result.Errors {
			report += fmt.Sprintf("  [ERROR] %s: %v\n", name, err)
		}
	}

	return report
}