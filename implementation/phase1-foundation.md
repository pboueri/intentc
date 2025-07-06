# Phase 1: Foundation and Core Structure

## Overview
Establish the basic Go project structure, CLI framework, and core abstractions for intentc.

## Goals
- [ ] Set up Go module and project structure
- [ ] Implement basic CLI with cobra
- [ ] Create core interfaces and types
- [ ] Set up comprehensive testing framework
- [ ] Implement git integration checks
- [ ] Create mock agent for testing

## Tasks

### 1.1 Project Setup
- [ ] Initialize Go module: `go mod init github.com/pboueri/intentc`
- [ ] Create directory structure:
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
- [ ] Define core types in `pkg/core/types.go`:
  - Intent
  - Target
  - Validation
  - GenerationID
  - BuildContext
  - AgentResponse

- [ ] Define interfaces in `pkg/core/interfaces.go`:
  - Agent interface
  - GitProvider interface
  - FileSystem interface
  - Validator interface

### 1.3 CLI Framework
- [ ] Implement cobra-based CLI in `pkg/cli/commands.go`
- [ ] Create command stubs for all planned commands
- [ ] Add global flags (--verbose, --dry-run, etc.)
- [ ] Implement version command

### 1.4 Git Integration
- [ ] Implement GitProvider in `pkg/git/git.go`:
  - Check if in git repo
  - Check if working tree is clean
  - Create commits with prefixes
  - Get current branch
  - List commits by prefix

- [ ] Create comprehensive git tests using temporary repos

### 1.5 Mock Agent
- [ ] Implement mock agent in `pkg/agent/mock.go`:
  - Copy files from fixtures directory
  - Support configurable responses
  - Simulate delays and errors for testing

### 1.6 Testing Infrastructure
- [ ] Set up testutil package with helpers:
  - Create/destroy temp git repos
  - Load test fixtures
  - Assert file contents
  - Mock agent configuration

- [ ] Create unit tests for all components
- [ ] Create integration tests for git operations
- [ ] Ensure 80%+ code coverage

## Success Criteria
- [ ] `go build ./cmd/intentc` produces working binary
- [ ] `intentc version` shows version info
- [ ] All commands show help text
- [ ] Git integration detects repo status correctly
- [ ] Mock agent can simulate basic code generation
- [ ] All tests pass with >80% coverage

## CLAUDE.md Updates
After Phase 1, add:
- Build command: `go build ./cmd/intentc`
- Test commands: `go test ./...` and `go test -cover ./...`
- Project structure overview
- Core interfaces documentation