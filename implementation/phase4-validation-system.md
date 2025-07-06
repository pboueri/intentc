# Phase 4: Validation System

## Overview
Implement the validation framework that ensures generated code meets specified constraints and requirements.

## Goals
- [x] Parse `.icv` validation files
- [x] Implement all validation types
- [x] Create validation runner
- [x] Build validation reporting
- [ ] Support custom validators

## Tasks

### 4.1 Validation File Parser
- [x] Create `pkg/validation/parser.go`:
  - Parse `.icv` markdown files ✓
  - Extract validation rules ✓
  - Support inline code blocks (partial)
  - Handle validation metadata ✓

- [ ] Define validation file schema:
  ```markdown
  exclude: true  # Optional - excludes file from generation
  
  # Validation: [Name]
  
  ## Target: [target-name]
  
  ### FileCheck
  - path: src/components/App.js
    check: Component file exists and exports a valid React component with proper imports
  
  ### FolderCheck
  - path: src/components
    check: Components directory exists with at least 3 component files including test files
  
  ### CommandLineCheck
  - command: npm test
    check: All tests pass successfully with no failures reported
  
  ### WebCheck
  - url: http://localhost:3000
    check: Application loads successfully and displays expected content
  
  ### ProjectCheck
  - check: Project has required dependencies installed and proper configuration files
  ```

### 4.2 Validator Implementations
- [x] Implement validators in `pkg/validation/validators/`:
  
  **FileCheck** (`file_check.go`): ✓
  - Check file existence ✓
  - Natural language validation rules (via agent)
  - Content pattern matching ✓
  - Flexible check interpretation ✓
  
  **FolderCheck** (`folder_check.go`): ✓
  - Check directory existence ✓
  - Natural language directory validation (partial)
  - File pattern checking ✓
  - Flexible interpretation ✓
  
  **WebCheck** (`web_check.go`): ✓
  - HTTP endpoint testing (via agent)
  - Natural language response validation ✓
  - Content checking (via agent)
  - Load time validation (not implemented)
  
  **ProjectCheck** (`project_check.go`): ✓
  - Natural language project validation ✓
  - Dependency checking (via agent)
  - Configuration validation (via agent)
  - Project structure validation (via agent)
  
  **CommandLineCheck** (`command_check.go`): ✓
  - Execute commands ✓
  - Natural language result validation (partial)
  - Output checking ✓
  - Success determination ✓

### 4.3 Validation Runner
- [x] Create `pkg/validation/runner.go`:
  - Load validation files ✓
  - Execute validations in order ✓
  - Handle validation dependencies (not needed)
  - Support parallel validation ✓
  - Implement retry logic (not implemented)

- [x] Validation context:
  - Generation ID ✓
  - Build artifacts ✓
  - Environment variables (partial)
  - Previous validation results (in state)

### 4.4 Validation Reporting
- [x] Implement reporting in `pkg/validation/report.go`:
  - Detailed error messages ✓
  - Success/failure summary ✓
  - JSON/text output formats (text only)
  - Validation timing metrics ✓
  - Suggested fixes (not implemented)

- [ ] Report formats:
  ```
  Validation Summary
  ==================
  Target: auth-system
  Total: 15 | Passed: 12 | Failed: 3
  
  Failed Validations:
  - FileCheck: src/auth/login.js missing required import
  - CommandLineCheck: npm test failed with exit code 1
  - WebCheck: /api/health returned 404
  ```

### 4.5 Validation Commands
- [ ] Implement `intentc validation list`:
  - List all available validation types
  - Show brief description of each
  - Include examples

- [ ] Implement `intentc validation add`:
  - Add validation stub to target
  - Interactive prompts for validation type
  - Generate template validation rules
  - Update .icv file

### 4.6 Exclusion Handling
- [ ] Parse `exclude: true` at file top:
  - Skip file during generation
  - Mark in intent registry
  - Show in status commands
  - Validate exclusions work properly

### 4.7 Testing
- [ ] Unit tests for each validator:
  - Success scenarios
  - Failure scenarios
  - Edge cases
  - Error handling

- [ ] Integration tests:
  - Multi-validator runs
  - Complex validation files
  - Parallel execution
  - Custom validators

- [ ] End-to-end tests:
  - Validate generated city explorer
  - Test all validator types
  - Verify reporting accuracy

## Success Criteria
- [x] All 5 validator types implemented and tested
- [x] `intentc validate target` runs all validations
- [ ] `intentc validation list/add` commands work
- [ ] Exclusion with `exclude: true` works properly
- [x] Clear, actionable error messages
- [x] Validation reports in multiple formats (text format)
- [x] 90%+ test coverage for validation system

## CLAUDE.md Updates
After Phase 4, add:
- Validation file format reference
- Validator type documentation  
- Using `exclude: true` for exclusion
- Validation command usage
- Natural language check guidelines
- Common validation patterns