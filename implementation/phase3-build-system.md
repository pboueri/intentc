# Phase 3: Build System

## Overview
Implement the core build system that transforms intents into code using the Claude CLI agent.

## Goals
- [ ] Integrate with Claude CLI for code generation
- [x] Implement build orchestration
- [x] Handle generation IDs and tracking
- [ ] Create build caching system
- [x] Implement incremental builds

## Tasks

### 3.1 Claude Agent Integration
- [ ] Implement Claude agent in `pkg/agent/claude.go`:
  - Shell out to `claude` CLI command
  - Format intent as prompt
  - Capture generated code output
  - Handle errors and timeouts
  - Parse Claude's response

- [ ] Create agent prompt templates:
  - System prompt explaining intentc
  - Product-focused intent prompts
  - Context about project structure
  - Previous generation history
  - Natural language validation rules for the agent to verify
  - Emphasize user experience over implementation details

### 3.2 Build Orchestrator
- [x] Create `pkg/build/orchestrator.go`:
  - Resolve build order from DAG ✓
  - Execute builds in parallel where possible ✓
  - Track build progress ✓
  - Handle build failures ✓
  - Rollback on errors (partial - status tracking)

- [x] Implement build strategies:
  - Sequential only (parallel disabled when using git for state tracking) ✓
  - Incremental (only changed targets) ✓
  - Git state verification before each target (partial)

- [x] Additional implemented features:
  - Clean command with dependency tracking ✓
  - Dry-run mode for build and clean ✓
  - Force rebuild option ✓

### 3.3 Generation ID System
- [x] Implement in `pkg/build/generation.go`:
  - Generate unique IDs (timestamp-based) ✓
  - Track generation metadata:
    - Target name ✓
    - Intent hash (partial)
    - Build timestamp ✓
    - Agent used ✓
    - Success/failure status ✓

- [x] Store generation history:
  - `.intentc/state/` directory ✓
  - JSON metadata files ✓
  - Link to git commits (partial)

### 3.4 Build Context
- [ ] Create build context in `pkg/build/context.go`:
  - Current project state
  - Available dependencies
  - Previous generations
  - Environment variables
  - Configuration values

- [ ] Context injection for agents:
  - Project structure
  - Existing code
  - Import statements
  - Package dependencies

### 3.5 Build Cache
- [ ] Implement caching in `pkg/build/cache.go`:
  - Cache key: intent hash + context hash
  - Store in `.intentc/cache/`
  - Invalidation rules
  - Cache cleanup commands

- [ ] Cache strategies:
  - Full output caching
  - Partial result caching
  - Agent response caching

### 3.6 Natural Language Validation Integration
- [ ] Pass validation rules to agent:
  - Include .icv file contents in prompts
  - Ask agent to verify generated code meets checks
  - Agent self-validates during generation

### 3.7 Testing
- [ ] Unit tests:
  - Mock Claude agent responses
  - Build orchestration logic
  - Generation ID uniqueness
  - Cache hit/miss scenarios

- [ ] Integration tests:
  - Sequential multi-target builds
  - Build failure handling
  - Incremental builds
  - Natural language validation by agent

- [ ] End-to-end tests:
  - Build simple project with mock agent
  - Verify generated files
  - Check git commits
  - Test cache effectiveness

## Success Criteria
- [ ] `intentc build target` generates code via Claude CLI
- [x] Sequential builds maintain clean git state
- [x] Generation IDs are unique and tracked properly
- [ ] Build cache improves performance on repeated builds
- [x] Failed builds roll back cleanly (status tracking)
- [ ] Natural language validations checked by agent
- [x] 85%+ test coverage for build system

## CLAUDE.md Updates
After Phase 3, add:
- Build command usage examples
- Generation ID format documentation
- Cache management commands
- Troubleshooting build failures
- Claude CLI integration notes