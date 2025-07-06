# Phase 2: Intent System

## Overview
Implement the intent file parsing, dependency resolution, and target system that forms the core of intentc.

## Goals
- [ ] Parse `.ic` intent files (markdown format)
- [ ] Build dependency graph for features
- [ ] Implement target resolution
- [ ] Create intent file validation
- [ ] Implement `intentc check` command

## Tasks

### 2.1 Intent File Parser
- [ ] Create `pkg/intent/parser.go`:
  - Parse markdown structure
  - Extract metadata (dependencies, tags)
  - Parse target definitions
  - Support both project and feature intents

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

- [ ] Support feature folder structure:
  - `intent/project/` - Project-level intents (dependencies optional)
  - `intent/features/*/` - Feature folders with colocated .ic and .icv files
  - Auto-discovery of intent files

### 2.2 Dependency Graph
- [ ] Implement DAG in `pkg/intent/dag.go`:
  - Add nodes (features/targets)
  - Add edges (dependencies)
  - Detect cycles
  - Topological sort for build order
  - Find affected targets

- [ ] Create visualization for `intentc status`:
  - ASCII tree view
  - Show build status
  - Highlight dependencies

### 2.3 Target System
- [ ] Implement target resolver in `pkg/intent/target.go`:
  - Resolve target from intent files
  - Expand wildcards (e.g., `*`, `**`)
  - Handle target aliases
  - Check if target is up-to-date

- [ ] Create target registry:
  - Track all available targets
  - Store target metadata
  - Cache parsed intents

### 2.4 Intent Validation Command
- [ ] Implement `intentc check` command:
  - Parse intent files for syntax errors
  - Validate dependency references exist
  - Check for circular dependencies
  - Suggest fixes for common issues
  - Report parsing errors clearly

### 2.5 Testing
- [ ] Unit tests for parser:
  - Valid intent files
  - Invalid syntax
  - Edge cases

- [ ] Integration tests:
  - Multi-file dependencies
  - Circular dependency detection
  - Template expansion

- [ ] End-to-end tests:
  - Parse example project intents
  - Build dependency graph
  - Resolve all targets

## Success Criteria
- [ ] Can parse complex intent files without errors
- [ ] Dependency cycles are detected and reported
- [ ] `intentc status` shows accurate dependency graph
- [ ] `intentc check` validates intent files properly
- [ ] Target resolution works with wildcards
- [ ] Project-level intents work without dependencies
- [ ] 85%+ test coverage for intent system

## CLAUDE.md Updates
After Phase 2, add:
- Intent file format documentation
- Dependency syntax examples
- Target naming conventions
- Project vs feature intent differences
- Using `intentc check` for validation