package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src/intent"
)

var (
	checkVerbose bool
	checkFix     bool
)

func init() {
	checkCmd.Flags().BoolVarP(&checkVerbose, "verbose", "v", false, "Show detailed validation information")
	checkCmd.Flags().BoolVar(&checkFix, "fix", false, "Attempt to fix common issues")
}

var checkCmd = &cobra.Command{
	Use:   "check [target]",
	Short: "Validate intent files",
	Long:  `Validate intent files for syntax errors, missing dependencies, and circular dependencies.`,
	Args:  cobra.MaximumNArgs(1),
	RunE:  runCheck,
}

type CheckResult struct {
	Target   string
	Valid    bool
	Errors   []string
	Warnings []string
	Suggestions []string
}

func runCheck(cmd *cobra.Command, args []string) error {
	projectRoot, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("failed to get current directory: %w", err)
	}

	// Check if .intentc exists
	configPath := filepath.Join(projectRoot, ".intentc")
	if _, err := os.Stat(configPath); os.IsNotExist(err) {
		return fmt.Errorf("not in an intentc project (no .intentc found). Run 'intentc init' first")
	}

	// Create registry and parser
	registry := intent.NewTargetRegistry(projectRoot)
	parser := intent.NewParser()

	// Load all targets
	if err := registry.LoadTargets(); err != nil {
		return fmt.Errorf("failed to load targets: %w", err)
	}

	var results []CheckResult

	if len(args) > 0 {
		// Check specific target
		target, exists := registry.GetTarget(args[0])
		if !exists {
			return fmt.Errorf("target %s not found", args[0])
		}
		result := checkTarget(target, registry, parser)
		results = append(results, result)
	} else {
		// Check all targets
		for _, target := range registry.GetAllTargets() {
			result := checkTarget(target, registry, parser)
			results = append(results, result)
		}
	}

	// Check for circular dependencies
	dag := intent.NewDAG()
	allTargets := registry.GetAllTargets()
	var intents []*intent.IntentFile
	for _, target := range allTargets {
		if target.Intent != nil {
			intents = append(intents, target.Intent)
		}
	}

	if err := dag.BuildFromIntents(intents); err != nil {
		fmt.Printf("\nDependency Graph Error: %v\n", err)
	} else {
		if hasCycle, cycle := dag.DetectCycles(); hasCycle {
			fmt.Printf("\nCircular Dependency Detected: %s\n", strings.Join(cycle, " -> "))
		}
	}

	// Print results
	printCheckResults(results, checkVerbose)

	// Return error if any validation failed
	hasErrors := false
	for _, result := range results {
		if !result.Valid {
			hasErrors = true
			break
		}
	}

	if hasErrors {
		return fmt.Errorf("validation failed")
	}

	fmt.Println("\nAll checks passed ✓")
	return nil
}

func checkTarget(target *intent.TargetInfo, registry *intent.TargetRegistry, parser *intent.Parser) CheckResult {
	result := CheckResult{
		Target:   target.Name,
		Valid:    true,
		Errors:   []string{},
		Warnings: []string{},
		Suggestions: []string{},
	}

	if target.Intent == nil {
		result.Valid = false
		result.Errors = append(result.Errors, "No intent file found")
		return result
	}

	// Check intent file syntax
	if target.Intent.Name == "" {
		result.Valid = false
		result.Errors = append(result.Errors, "Intent file missing name header")
		result.Suggestions = append(result.Suggestions, "Add '# Feature: [Name]' or '# Project: [Name]' header")
	}

	// Check for empty content
	if strings.TrimSpace(target.Intent.RawContent) == "" {
		result.Valid = false
		result.Errors = append(result.Errors, "Intent file is empty")
	}

	// Check dependencies
	for _, dep := range target.Intent.Dependencies {
		if _, exists := registry.GetTarget(dep); !exists {
			result.Valid = false
			result.Errors = append(result.Errors, fmt.Sprintf("Missing dependency: %s", dep))
			result.Suggestions = append(result.Suggestions, fmt.Sprintf("Create intent file for '%s' or remove from dependencies", dep))
		}
	}

	// Check for recommended sections
	if target.Intent.Description == "" {
		result.Warnings = append(result.Warnings, "Missing Description section")
		result.Suggestions = append(result.Suggestions, "Add '## Description' section with product-focused description")
	}

	if target.Intent.Type == intent.IntentTypeFeature {
		if target.Intent.UserExperience == "" {
			result.Warnings = append(result.Warnings, "Missing User Experience section")
			result.Suggestions = append(result.Suggestions, "Add '## User Experience' section describing how users interact with this feature")
		}

		if target.Intent.QualityGoals == "" {
			result.Warnings = append(result.Warnings, "Missing Quality Goals section")
			result.Suggestions = append(result.Suggestions, "Add '## Quality Goals' section with performance and usability requirements")
		}
	}

	// Check validation files
	if len(target.ValidationFiles) == 0 {
		result.Warnings = append(result.Warnings, "No validation files found")
		result.Suggestions = append(result.Suggestions, fmt.Sprintf("Create %s.icv file with validation rules", target.Name))
	}

	// Check file naming convention
	expectedPath := filepath.Join(filepath.Dir(target.Intent.Path), target.Name+".ic")
	if target.Intent.Path != expectedPath {
		result.Warnings = append(result.Warnings, fmt.Sprintf("Intent file name doesn't match target name (expected %s)", filepath.Base(expectedPath)))
	}

	return result
}

func printCheckResults(results []CheckResult, verbose bool) {
	fmt.Println("\nIntent File Validation Report")
	fmt.Println("=============================")

	totalTargets := len(results)
	validTargets := 0
	totalErrors := 0
	totalWarnings := 0

	for _, result := range results {
		if result.Valid {
			validTargets++
		}
		totalErrors += len(result.Errors)
		totalWarnings += len(result.Warnings)
	}

	fmt.Printf("\nSummary: %d targets checked, %d valid, %d errors, %d warnings\n\n", 
		totalTargets, validTargets, totalErrors, totalWarnings)

	for _, result := range results {
		status := "✓"
		statusColor := "\033[32m" // green
		if !result.Valid {
			status = "✗"
			statusColor = "\033[31m" // red
		} else if len(result.Warnings) > 0 {
			status = "!"
			statusColor = "\033[33m" // yellow
		}

		fmt.Printf("%s[%s] %s%s\033[0m\n", statusColor, status, result.Target, "")

		if verbose || !result.Valid {
			// Print errors
			for _, err := range result.Errors {
				fmt.Printf("  \033[31mERROR:\033[0m %s\n", err)
			}

			// Print warnings
			if verbose {
				for _, warn := range result.Warnings {
					fmt.Printf("  \033[33mWARN:\033[0m %s\n", warn)
				}
			}

			// Print suggestions
			if len(result.Suggestions) > 0 {
				fmt.Println("  Suggestions:")
				for _, suggestion := range result.Suggestions {
					fmt.Printf("    • %s\n", suggestion)
				}
			}

			if !result.Valid || (verbose && len(result.Warnings) > 0) {
				fmt.Println()
			}
		}
	}

	if !verbose && totalWarnings > 0 {
		fmt.Printf("\nRun with --verbose to see %d warnings\n", totalWarnings)
	}
}