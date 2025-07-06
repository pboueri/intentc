# Phase 6: REPL and Refinement

## Overview
Implement the interactive REPL for iterative refinement of generated code, allowing real-time collaboration with the AI agent.

## Goals
- [ ] Build interactive REPL interface
- [ ] Implement refinement commands
- [ ] Create conversation context management
- [ ] Handle incremental changes
- [ ] Support refinement history

## Tasks

### 6.1 REPL Core
- [ ] Create `pkg/repl/repl.go`:
  - Command loop with readline support
  - Command parsing and dispatch
  - Session state management
  - Exit handling

- [ ] REPL commands:
  ```
  > help              - Show available commands
  > show              - Display current generation
  > edit <file>       - Show file for discussion
  > refine <prompt>   - Request changes
  > validate          - Run validations
  > diff              - Show changes
  > commit            - Commit changes
  > rollback          - Undo last refinement
  > history           - Show refinement history
  > exit              - Exit REPL
  ```

### 6.2 Refinement Context
- [ ] Implement in `pkg/repl/context.go`:
  - Current generation state
  - File modifications
  - Conversation history
  - Validation results
  - Agent memory

- [ ] Context persistence:
  - Save/restore sessions
  - Track refinement chains
  - Link to generation IDs

### 6.3 Interactive Agent & Intent Management
- [ ] Create `pkg/repl/agent.go`:
  - Maintain conversation context
  - Format refinement prompts
  - Include relevant code context
  - Handle incremental updates
  - Track which changes affect intents

- [ ] Intent update detection:
  - Monitor refinements for intent changes
  - Detect new requirements or features
  - Update intent files automatically
  - Preserve user's original intent structure

- [ ] Dual-mode updates:
  - Update generated code files
  - Update intent/validation files when needed
  - Clear indication of what's being changed
  - Maintain consistency between both

### 6.4 File Operations
- [ ] Implement in `pkg/repl/files.go`:
  - Display files with syntax highlighting
  - Show diffs between versions
  - Track modified files
  - Preview changes

- [ ] File display features:
  - Line numbers
  - Syntax highlighting
  - Truncation for large files
  - Search within files

### 6.5 Refinement History
- [ ] Create `pkg/repl/history.go`:
  - Track all refinements
  - Store prompts and responses
  - Link changes to refinements
  - Track intent file updates
  - Enable replay/review

- [ ] History features:
  - Browse past refinements
  - Re-apply refinements
  - Export conversation
  - Search history
  - Show intent vs code changes

### 6.6 Validation Integration
- [ ] Integrate validation in REPL:
  - Run validations on demand
  - Show validation results inline
  - Suggest fixes for failures
  - Re-validate after refinements

- [ ] Validation workflow:
  - Auto-validate after changes
  - Filter by validation type
  - Focus on failures
  - Quick fix suggestions

### 6.7 Testing
- [ ] Unit tests:
  - Command parsing
  - Context management
  - History operations
  - File display

- [ ] Integration tests:
  - Full REPL session
  - Multi-refinement workflow
  - Validation integration
  - State persistence

- [ ] End-to-end tests:
  - Refine city explorer features
  - Fix validation failures
  - Complete refinement cycle
  - Session save/restore

## Success Criteria
- [ ] `intentc refine` launches interactive REPL
- [ ] Can refine both generated code and intents
- [ ] Intent files updated automatically when needed
- [ ] Context maintained across refinements
- [ ] Clear display of changes and diffs
- [ ] Validation integrated seamlessly
- [ ] 80%+ test coverage for REPL system

## CLAUDE.md Updates
After Phase 6, add:
- REPL command reference
- Refinement workflow guide
- How intent updates work
- Best practices for prompts
- Session management tips
- Troubleshooting REPL issues