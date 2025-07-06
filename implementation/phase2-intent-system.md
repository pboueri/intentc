# Phase 2: Intent System

## Overview
Implement the intent file parsing, dependency resolution, and target system that forms the core of intentc.

## Goals
- [x] Parse `.ic` intent files (markdown format)
- [x] Build dependency graph for features
- [x] Implement target resolution
- [x] Create intent file validation
- [x] Implement `intentc check` command

## Tasks

### 2.1 Intent File Parser
- [x] Create `pkg/intent/parser.go`:
  - Parse markdown structure ✓
  - Extract metadata (dependencies, tags) ✓
  - Parse target definitions ✓
  - Support both project and feature intents ✓

- [ ] Define intent file schema:
  ```markdown
  # Feature: [Name]  OR  # Project: [Name]
  
  ## Dependencies  (optional for project-level intents)
  - feature1
  - feature2
  
  ## Target: [target-name]
  
  ## Description
  [Product-focused description of what users should experience]
  
  ## User Experience
  [How users interact with this feature]
  
  ## Quality Goals
  [Performance, usability, and other non-functional requirements]
  ```

- [x] Support feature folder structure:
  - `intent/project/` - Project-level intents (dependencies optional) ✓
  - `intent/features/*/` - Feature folders with colocated .ic and .icv files ✓
  - Auto-discovery of intent files ✓

### 2.2 Dependency Graph
- [x] Implement DAG in `pkg/intent/dag.go`:
  - Add nodes (features/targets) ✓
  - Add edges (dependencies) ✓
  - Detect cycles ✓
  - Topological sort for build order ✓
  - Find affected targets ✓

- [x] Create visualization for `intentc status`:
  - ASCII tree view ✓
  - Show build status ✓
  - Highlight dependencies ✓

### 2.3 Target System
- [x] Implement target resolver in `pkg/intent/target.go`:
  - Resolve target from intent files ✓
  - Expand wildcards (e.g., `*`, `**`) ✓
  - Handle target aliases ✓
  - Check if target is up-to-date ✓

- [x] Create target registry:
  - Track all available targets ✓
  - Store target metadata ✓
  - Cache parsed intents ✓

### 2.4 Intent Validation Command
- [x] Implement `intentc check` command:
  - Parse intent files for syntax errors ✓
  - Validate dependency references exist ✓
  - Check for circular dependencies ✓
  - Suggest fixes for common issues ✓
  - Report parsing errors clearly ✓

### 2.5 Testing
- [x] Unit tests for parser:
  - Valid intent files ✓
  - Invalid syntax ✓
  - Edge cases ✓

- [x] Integration tests:
  - Multi-file dependencies ✓
  - Circular dependency detection ✓
  - Template expansion ✓

- [x] End-to-end tests:
  - Parse example project intents ✓
  - Build dependency graph ✓
  - Resolve all targets ✓

## Success Criteria
- [x] Can parse complex intent files without errors
- [x] Dependency cycles are detected and reported
- [x] `intentc status` shows accurate dependency graph
- [x] `intentc check` validates intent files properly
- [x] Target resolution works with wildcards
- [x] Project-level intents work without dependencies
- [x] 85%+ test coverage for intent system

## CLAUDE.md Updates
After Phase 2, add:
- Intent file format documentation
- Dependency syntax examples
- Target naming conventions
- Project vs feature intent differences
- Using `intentc check` for validation