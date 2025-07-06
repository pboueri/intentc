# Phase 1: Foundation and Core Structure

## Overview
Establish the basic Go project structure, CLI framework, and core abstractions for intentc.

## Goals
- [x] Set up Go module and project structure
- [x] Implement basic CLI with cobra
- [x] Create core interfaces and types
- [x] Set up comprehensive testing framework
- [x] Implement git integration checks
- [x] Create mock agent for testing

## Tasks

### 1.1 Project Setup
- [x] Initialize Go module: `go mod init github.com/pboueri/intentc`
- [x] Create directory structure (modified to use src/ for cleaner organization):
  ```
  cmd/
    intentc/
      main.go
  pkg/
    cli/
      commands.go
      init.go
      build.go
      clean.go
      validate.go
      refine.go
      commit.go
      status.go
      config.go
    core/
      types.go
      interfaces.go
      errors.go
    git/
      git.go
      git_test.go
    agent/
      agent.go
      claude.go
      mock.go
    testutil/
      git.go
      fixtures.go
  ```

### 1.2 Core Types and Interfaces
- [x] Define core types in `src/types.go`:
  - Intent
  - Target
  - Validation
  - GenerationID (as part of BuildResult)
  - BuildContext (as BuildResult)
  - AgentResponse (as BuildResult)

- [x] Define interfaces in respective packages:
  - Agent interface (src/agent/interface.go)
  - GitProvider interface (src/git/interface.go as GitManager)
  - FileSystem interface (using standard os package)
  - Validator interface (src/validation/interface.go)

### 1.3 CLI Framework
- [x] Implement cobra-based CLI in `src/cmd/root.go`
- [x] Create command stubs for all planned commands
- [ ] Add global flags (--verbose, --dry-run, etc.) - planned for later phases
- [ ] Implement version command - planned for later phases

### 1.4 Git Integration
- [x] Implement GitProvider in `src/git/git.go`:
  - Check if in git repo
  - Check if working tree is clean (via GetStatus)
  - Create commits with prefixes
  - Get current branch
  - List commits by prefix (via GetLog)

- [x] Create comprehensive git tests using temporary repos

### 1.5 Mock Agent
- [x] Implement mock agent in `src/agent/mock.go`:
  - [x] Support configurable responses via function fields
  - [x] Default implementations for all Agent interface methods
  - [ ] Copy files from fixtures directory - planned for later phases
  - [ ] Simulate delays and errors for testing - can be added via function fields

### 1.6 Testing Infrastructure
- [x] Set up testing helpers directly in test files:
  - Create/destroy temp git repos (in git_test.go)
  - Load test fixtures (inline in tests)
  - Assert file contents (using testify)
  - Mock agent configuration (MockAgent struct)

- [x] Create unit tests for all components:
  - Parser tests (intent_test.go, validation_test.go)
  - Git tests (git_test.go)
  - Command tests (init_test.go)
- [x] Create integration tests for git operations
- [ ] Ensure 80%+ code coverage - to be measured

## Success Criteria
- [x] `go build .` produces working binary
- [ ] `intentc version` shows version info - planned for later phases
- [x] All commands show help text
- [x] Git integration detects repo status correctly
- [x] Mock agent can simulate basic code generation
- [x] All tests pass
- [ ] >80% coverage - to be measured

## CLAUDE.md Updates
After Phase 1, add:
- [x] Build command: `go build .`
- [x] Test commands: `go test ./...` and `go test -cover ./...`
- [x] Project structure overview
- [x] Core interfaces documentation
- [x] Testing requirements for all future phases