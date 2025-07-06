package intent

import (
	"fmt"
	"path/filepath"
	"regexp"
	"strings"
)

type TargetResolver struct {
	registry *TargetRegistry
}

func NewTargetResolver(registry *TargetRegistry) *TargetResolver {
	return &TargetResolver{
		registry: registry,
	}
}

// ResolveTargets resolves target patterns to actual targets
// Supports:
// - Exact names: "auth-system"
// - Wildcards: "auth-*", "*-system", "*"
// - Recursive wildcards: "features/**", "**/*-test"
// - Multiple targets: ["auth", "user", "api"]
func (r *TargetResolver) ResolveTargets(patterns []string) ([]*TargetInfo, error) {
	if len(patterns) == 0 {
		// If no patterns specified, return all targets
		return r.registry.GetAllTargets(), nil
	}
	
	resolved := make(map[string]*TargetInfo)
	
	for _, pattern := range patterns {
		matches, err := r.resolvePattern(pattern)
		if err != nil {
			return nil, fmt.Errorf("failed to resolve pattern %s: %w", pattern, err)
		}
		
		if len(matches) == 0 {
			return nil, fmt.Errorf("no targets match pattern: %s", pattern)
		}
		
		for _, target := range matches {
			resolved[target.Name] = target
		}
	}
	
	// Convert map to slice
	var result []*TargetInfo
	for _, target := range resolved {
		result = append(result, target)
	}
	
	return result, nil
}

func (r *TargetResolver) resolvePattern(pattern string) ([]*TargetInfo, error) {
	// Handle exact match first
	if !strings.Contains(pattern, "*") {
		target, exists := r.registry.GetTarget(pattern)
		if !exists {
			return nil, nil
		}
		return []*TargetInfo{target}, nil
	}
	
	// Convert glob pattern to regex
	regexPattern := r.globToRegex(pattern)
	regex, err := regexp.Compile(regexPattern)
	if err != nil {
		return nil, fmt.Errorf("invalid pattern: %w", err)
	}
	
	var matches []*TargetInfo
	for _, target := range r.registry.GetAllTargets() {
		if r.matchesPattern(target, pattern, regex) {
			matches = append(matches, target)
		}
	}
	
	return matches, nil
}

func (r *TargetResolver) matchesPattern(target *TargetInfo, pattern string, regex *regexp.Regexp) bool {
	// Match against target name
	if regex.MatchString(target.Name) {
		return true
	}
	
	// If pattern contains path separators, also match against path
	if strings.Contains(pattern, "/") && target.Intent != nil {
		// Get relative path from intent directory
		relPath, err := filepath.Rel(filepath.Join(r.registry.projectRoot, "intent"), target.Intent.Path)
		if err == nil {
			// Remove .ic extension for matching
			relPath = strings.TrimSuffix(relPath, ".ic")
			if regex.MatchString(relPath) {
				return true
			}
		}
	}
	
	return false
}

func (r *TargetResolver) globToRegex(pattern string) string {
	// Escape special regex characters except * and /
	escaped := regexp.QuoteMeta(pattern)
	
	// Convert back the escaped * to proper regex
	escaped = strings.ReplaceAll(escaped, `\*\*`, ".*")     // ** matches any path
	escaped = strings.ReplaceAll(escaped, `\*`, ".*")      // * matches any characters
	
	// Anchor the pattern
	return "^" + escaped + "$"
}

// CheckIfUpToDate checks if a target is up to date based on file modifications
func (r *TargetResolver) CheckIfUpToDate(target *TargetInfo, generatedFiles []string) (bool, error) {
	if target.Intent == nil {
		return false, fmt.Errorf("target %s has no associated intent", target.Name)
	}
	
	// Get intent file modification time
	intentStat, err := r.registry.getFileStat(target.Intent.Path)
	if err != nil {
		return false, fmt.Errorf("failed to stat intent file: %w", err)
	}
	
	// Check if any generated files are older than the intent
	for _, genFile := range generatedFiles {
		genStat, err := r.registry.getFileStat(genFile)
		if err != nil {
			// Generated file doesn't exist, so not up to date
			return false, nil
		}
		
		if genStat.ModTime().Before(intentStat.ModTime()) {
			// Generated file is older than intent
			return false, nil
		}
	}
	
	// Check dependencies
	for _, depName := range target.Intent.Dependencies {
		dep, exists := r.registry.GetTarget(depName)
		if !exists {
			return false, fmt.Errorf("dependency %s not found", depName)
		}
		
		if dep.Intent != nil {
			depStat, err := r.registry.getFileStat(dep.Intent.Path)
			if err != nil {
				return false, fmt.Errorf("failed to stat dependency intent: %w", err)
			}
			
			// Check if any generated files are older than the dependency
			for _, genFile := range generatedFiles {
				genStat, err := r.registry.getFileStat(genFile)
				if err != nil {
					return false, nil
				}
				
				if genStat.ModTime().Before(depStat.ModTime()) {
					// Generated file is older than dependency
					return false, nil
				}
			}
		}
	}
	
	return true, nil
}

// ExpandAliases expands target aliases to their actual targets
func (r *TargetResolver) ExpandAliases(names []string) []string {
	expanded := make(map[string]bool)
	
	for _, name := range names {
		// Check if it's an alias
		if alias, exists := r.registry.aliases[name]; exists {
			for _, target := range alias {
				expanded[target] = true
			}
		} else {
			expanded[name] = true
		}
	}
	
	// Convert map to slice
	var result []string
	for name := range expanded {
		result = append(result, name)
	}
	
	return result
}