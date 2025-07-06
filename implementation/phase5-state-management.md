# Phase 5: State Management

## Overview
Implement Git-based state management with proper commit prefixes, generation tracking, and clean separation between intent and generated code.

## Goals
- [ ] Implement intent/generated commit separation
- [ ] Create generation state tracking
- [ ] Build commit message formatting
- [ ] Handle uncommitted changes
- [ ] Implement rollback capabilities

## Tasks

### 5.1 Git State Manager
- [ ] Enhance `pkg/git/state.go`:
  - Check for clean working tree
  - Stage files by type (intent vs generated)
  - Create commits with proper prefixes
  - Track generation history in git

- [ ] Commit prefix system:
  - `intent:` - Changes to `.ic` and `.icv` files
  - `generated:` - AI-generated code changes
  - Include generation ID in generated commits

### 5.2 File Classification
- [ ] Create `pkg/state/classifier.go`:
  - Identify intent files (`.ic`, `.icv`)
  - Identify generated files
  - Track file origins
  - Handle mixed changes

- [ ] Classification rules:
  - Intent directory contents → intent
  - Build outputs → generated
  - User modifications → prompt for classification

### 5.3 Generation State Store
- [ ] Implement in `pkg/state/store.go`:
  - Store in `.intentc/state/`
  - Layered generation IDs: `{command-id}-{target-id}`
  - Track per-generation:
    - Files created/modified
    - Source intent hash
    - Build timestamp
    - Validation results
    - Agent used
    - Parent command ID

- [ ] State queries:
  - Get files for generation ID
  - Find generation for file
  - List all generations
  - Compare generations
  - Group by command ID

### 5.4 Commit Management
- [ ] Create `pkg/git/commits.go`:
  - Format commit messages:
    ```
    intent: Add authentication system
    
    - Added login intent
    - Added validation rules
    - Updated dependencies
    ```
    ```
    generated: Build authentication system [gen-id: 20240115-123456-abc123-auth]
    
    - Generated login component
    - Created auth service
    - Added tests
    
    Built from: auth-system intent
    Agent: claude
    Command: 20240115-123456-abc123
    ```

- [ ] Commit operations:
  - Atomic commits
  - Rollback support
  - Cherry-pick generations
  - Squash related commits

### 5.5 Working Tree Management
- [ ] Implement in `pkg/state/worktree.go`:
  - Detect uncommitted changes
  - Stash/restore for builds
  - Conflict detection
  - Clean working tree validation

- [ ] Pre-build checks:
  - No uncommitted intent changes
  - No uncommitted generated changes
  - No merge conflicts
  - Valid git state

### 5.6 Rollback System
- [ ] Create `pkg/state/rollback.go`:
  - Rollback by generation ID
  - Rollback by target
  - Partial rollbacks
  - Preserve intent files

- [ ] Implement `intentc checkout`:
  - Checkout specific generation: `intentc checkout {target} {generation-id}`
  - Restore files to that generation state
  - Update working directory
  - Maintain git history

- [ ] Rollback strategies:
  - Revert commits
  - Reset to previous state
  - Interactive rollback
  - Dry-run mode

### 5.7 Testing
- [ ] Unit tests:
  - Commit prefix formatting
  - File classification logic
  - State store operations
  - Rollback scenarios

- [ ] Integration tests:
  - Multi-file commits
  - Mixed change handling
  - Generation tracking
  - Git operations

- [ ] End-to-end tests:
  - Build → commit → rollback cycle
  - State persistence
  - Conflict resolution
  - Clean tree enforcement

## Success Criteria
- [ ] Clean separation of intent and generated commits
- [ ] Layered generation IDs work properly
- [ ] Each target gets its own generation ID and commit
- [ ] `intentc commit` handles mixed changes correctly
- [ ] `intentc checkout` restores specific generations
- [ ] Working tree must be clean before builds
- [ ] 85%+ test coverage for state management

## CLAUDE.md Updates
After Phase 5, add:
- Commit prefix conventions
- Layered generation ID format
- State management best practices
- Using `intentc checkout` command
- Git workflow integration