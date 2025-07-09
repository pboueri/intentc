# Duplication Analysis Report

## Overview
This report identifies duplicated implementations across the intentc codebase that could be consolidated to improve maintainability and reduce potential bugs.

**Last Updated:** 2025-07-09

## Major Duplications

### 1. ~~Two DAG Implementations~~ ✅ FIXED
**Status:** RESOLVED

**Previous Issue:**
- `src/intent/dag.go` and `src/graph/dag.go` were nearly identical implementations

**Resolution:**
- The `src/intent/dag.go` file has been removed
- Only `src/graph/dag.go` remains, providing a single DAG implementation
- The intent package has been restructured to eliminate this duplication

### 2. Parser Package Duplications - PARTIALLY FIXED
**Status:** IMPROVED

**Current Structure:**
- `src/parser/parser.go` - General parser orchestrator
- `src/parser/intent.go` - Intent-specific parsing
- `src/parser/validation.go` - Validation-specific parsing
- `src/parser/targets.go` - NEW: Consolidated target registry

**Improvements Made:**
- The `src/intent/parser.go` has been removed
- Target registry functionality has been consolidated into `src/parser/targets.go`
- The parser package now uses `parser.NewTargetRegistry` consistently across the codebase

**Remaining Issues:**
- Markdown parsing logic still duplicated between intent and validation parsers
- File discovery patterns still appear in multiple places
- Dependency parsing logic remains duplicated

**Impact:** Low-Medium - Improved but some duplication remains.

**Recommendation:** Consider extracting common markdown parsing utilities.

### 3. Agent Package Structure - IMPROVED
**Status:** PARTIALLY FIXED

**Current Structure:**
- `src/agent/cli.go` - Generic CLI agent (base class)
- `src/agent/claude.go` - Claude-specific agent (extends CLIAgent)

**Improvements Made:**
- `ClaudeAgent` now properly extends `CLIAgent` using embedded struct composition
- Retry logic has been consolidated in the base `CLIAgent`
- The Claude agent only overrides methods where Claude-specific behavior is needed

**Remaining Issues:**
- Some template handling logic is still duplicated between agents
- File parsing has slight variations (`parseGeneratedFiles` vs `parseClaudeGeneratedFiles`)

**Impact:** Low - The inheritance structure is much cleaner now.

**Recommendation:** Consider making file parsing more configurable rather than overriding.

### 4. Validation Type Definitions
**Files:**
- `src/cmd/validation.go` - Hardcoded validation types and examples
- `src/validation/runner.go` - Validation execution
- `src/validation/interface.go` - Validation interfaces
- Various validation check implementations

**Duplicate Patterns:**
- Validation type definitions scattered across files
- Example generation logic in command layer
- Validation parameter handling in multiple places

**Impact:** Low-Medium - Makes it harder to add new validation types.

**Recommendation:** Centralize validation type registry with metadata and examples.

### 5. File Path Handling
**Duplicate Patterns Found In:**
- Intent file discovery in parser packages
- Validation file discovery logic
- Generated file detection in agents
- Project structure traversal

**Common Operations:**
- Converting relative to absolute paths
- Finding files with specific extensions
- Directory traversal with filters
- Path normalization

**Impact:** Low - Minor inefficiency and potential for inconsistent behavior.

**Recommendation:** Create a `fileutil` package with common operations.

### 6. Template/Prompt Generation
**Files:**
- `src/agent/cli.go` - `createBuildPrompt` method
- `src/agent/claude.go` - `createClaudeBuildPrompt` method
- `src/agent/templates.go` - Template definitions

**Duplicate Logic:**
- Template data preparation (converting validations to template format)
- Dependency string formatting
- Error handling for template execution
- Fallback logic when templates fail

**Impact:** Low-Medium - Makes it harder to maintain consistent prompt formats.

**Recommendation:** Extract common template handling into shared utilities.

## Minor Duplications

### Error Handling Patterns
- Similar error wrapping patterns across packages
- Repeated context timeout handling
- Common retry logic patterns

### Git Operations
- Git status checking before/after operations
- File change detection logic
- Commit message generation patterns

### Logging Patterns
- Similar debug/info/error logging patterns
- Progress reporting duplicated across commands

## Summary of Improvements

### Fixed Issues ✅
1. **DAG Duplication** - The duplicate DAG implementation in `src/intent/dag.go` has been removed
2. **Parser Consolidation** - Intent parser consolidated, target registry unified in `src/parser/targets.go`
3. **Agent Inheritance** - ClaudeAgent now properly extends CLIAgent using composition

### Partially Fixed Issues 🔧
1. **Parser Package** - Better organized but markdown parsing still duplicated
2. **Agent Structure** - Inheritance improved but some template/parsing duplication remains

### Remaining Issues ❌
1. **Validation Type Definitions** - Still scattered across multiple files
2. **File Path Handling** - Common operations still duplicated
3. **Template Generation** - Similar logic in multiple places
4. **Minor Patterns** - Error handling, git operations, logging patterns

## Updated Priority Recommendations

1. **Medium Priority:** Extract common markdown parsing utilities in parser package
2. **Low Priority:** Centralize validation type registry with metadata
3. **Low Priority:** Create utility packages for file operations and templates
4. **Low Priority:** Standardize error handling and logging patterns

## Progress Score
**Major Issues Fixed: 2/6 (33%)**
**Overall Improvement: ~40%** - The most critical duplication (DAG) has been resolved, and significant progress has been made on parser and agent structure.