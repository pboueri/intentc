# Phase 4: Validation System

## Overview
Implement the validation framework that ensures generated code meets specified constraints and requirements.

## Goals
- [ ] Parse `.icv` validation files
- [ ] Implement all validation types
- [ ] Create validation runner
- [ ] Build validation reporting
- [ ] Support custom validators

## Tasks

### 4.1 Validation File Parser
- [ ] Create `pkg/validation/parser.go`:
  - Parse `.icv` markdown files
  - Extract validation rules
  - Support inline code blocks
  - Handle validation metadata

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
- [ ] Implement validators in `pkg/validation/validators/`:
  
  **FileCheck** (`file_check.go`):
  - Check file existence
  - Natural language validation rules
  - Content pattern matching
  - Flexible check interpretation
  
  **FolderCheck** (`folder_check.go`):
  - Check directory existence
  - Natural language directory validation
  - File pattern checking
  - Flexible interpretation
  
  **WebCheck** (`web_check.go`):
  - HTTP endpoint testing
  - Natural language response validation
  - Content checking
  - Load time validation
  
  **ProjectCheck** (`project_check.go`):
  - Natural language project validation
  - Dependency checking
  - Configuration validation
  - Project structure validation
  
  **CommandLineCheck** (`command_check.go`):
  - Execute commands
  - Natural language result validation
  - Output checking
  - Success determination

### 4.3 Validation Runner
- [ ] Create `pkg/validation/runner.go`:
  - Load validation files
  - Execute validations in order
  - Handle validation dependencies
  - Support parallel validation
  - Implement retry logic

- [ ] Validation context:
  - Generation ID
  - Build artifacts
  - Environment variables
  - Previous validation results

### 4.4 Validation Reporting
- [ ] Implement reporting in `pkg/validation/report.go`:
  - Detailed error messages
  - Success/failure summary
  - JSON/text output formats
  - Validation timing metrics
  - Suggested fixes

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
- [ ] All 5 validator types implemented and tested
- [ ] `intentc validate target` runs all validations
- [ ] `intentc validation list/add` commands work
- [ ] Exclusion with `exclude: true` works properly
- [ ] Clear, actionable error messages
- [ ] Validation reports in multiple formats
- [ ] 90%+ test coverage for validation system

## CLAUDE.md Updates
After Phase 4, add:
- Validation file format reference
- Validator type documentation  
- Using `exclude: true` for exclusion
- Validation command usage
- Natural language check guidelines
- Common validation patterns