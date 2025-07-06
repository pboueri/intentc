# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

intentc is a "Compiler of Intent" - a tool that transforms loosely specified intents into precise code using AI coding agents, inspired by GNU Make's declarative approach to build management.

## Project Status

Implementation in progress:
- Phase 1 (Foundation) - Completed
- Phase 2 (Intent System) - Completed
- Phase 3 (Claude Agent) - Completed
  - Integrated Claude Code CLI for code generation
  - Implemented retry logic and error handling
  - Added configuration support for timeout and rate limiting
  - Comprehensive test coverage

## Architecture Overview

- **Language**: Go (chosen for simplicity, speed, and good standard library)
- **CLI Framework**: Cobra for command-line interface
- **Testing**: testify for assertions, comprehensive mocking
- **Core Concept**: Transform intent files (`.ic`) into working code using AI agents
- **Validation**: Built-in validation system using `.icv` files
- **State Management**: Git-based with append-only commit log

## Key Commands

### Build and Test
```bash
go build -o intentc .        # Build the intentc binary
go test ./...                # Run all tests
go test -cover ./...         # Run tests with coverage
go test -v ./src/parser      # Run tests for specific package
```

### intentc Commands (Implemented and Planned)
```bash
intentc init                 # Initialize project structure
intentc build {target}       # Build targets from intents
intentc clean {target}       # Clean generated files
intentc validate {target}    # Run validations
intentc refine              # Interactive REPL for refinement
intentc commit              # Commit using git
intentc status              # Show target status
intentc config              # Configure agents and models
```

## File Types

- **`.ic` files**: Intent definitions in markdown format
- **`.icv` files**: Validation constraints in structured markdown
- **Generation IDs**: Track different build iterations

## Development Workflow

1. Define intents in `.ic` files
2. Build targets using AI agents (default: Claude Code API)
3. Validate outputs against `.icv` files
4. Refine iteratively in REPL mode
5. Commit both intent and generated code

## Testing Requirements

IMPORTANT: Every phase of implementation MUST include comprehensive tests. Follow these guidelines:

1. **Test Coverage**: Each new component must have corresponding test files
2. **Test Organization**: Tests should be in the same package as the code they test
3. **Test Execution**: Run `go test ./...` after implementing each phase
4. **Mock Dependencies**: Use mock implementations for external dependencies (git, agents, etc.)
5. **Test Isolation**: Tests must not modify the actual project's git repository
6. **Test Types**:
   - Unit tests for individual functions and methods
   - Integration tests for command execution
   - Mock tests for agent interfaces

Example test structure for each phase:
- Parser tests: Test parsing of .ic and .icv files
- Git tests: Test git operations in isolated temporary repositories
- Command tests: Test CLI commands with proper setup/teardown
- Agent tests: Use mock agents to simulate AI interactions
- Validation tests: Test validation logic with mock data

## Important Implementation Notes

- Git is a prerequisite (tool won't work without it)
- Separate commit prefixes: `intent:` for intents, `generated:` for code
- Feature dependencies form a DAG
- Validation types: FileCheck, FolderCheck, WebCheck, ProjectCheck, CommandLineCheck
- Uses Claude Code CLI (`claude` command) for AI code generation
- Requires Claude Code to be installed and authenticated
- Configuration via `.intentc/config.yaml` supports timeout and retry settings
- `.intentc` is a directory (not a file) containing config.yaml and state/
- Init command handles migration from old .intentc file format automatically

## Code Structure

### Package Organization
```
src/
├── types.go            # Core type definitions (Intent, Validation, Feature, etc.)
├── agent/              # AI agent interfaces and mock implementation
├── builder/            # Build system implementation
├── cleaner/            # Clean command implementation
├── cmd/                # CLI commands (cobra-based)
├── config/             # Configuration management
├── git/                # Git integration (GitManager interface)
├── intent/             # Intent parsing and DAG management
├── parser/             # .ic and .icv file parsers
├── state/              # State management (git-based)
└── validation/         # Validation system
```

### Key Interfaces
- **Agent**: AI coding agent interface (`src/agent/agent.go`)
- **GitManager**: Version control operations (`src/git/git.go`)
- **StateManager**: State persistence (`src/state/state.go`)
- **Validator**: Validation execution (`src/validation/validator.go`)

### Implementation Phases
- `implementation/phase1.md` through `implementation/phase7.md`: Detailed implementation plans
- Phase 1 (Foundation) - Completed
- Phases 2-7 - Planned with detailed specifications

## Key Directories

- `bootstrap/`: Contains product specifications and future ideas
- `bootstrap/product_specs.md`: Detailed product specifications
- `bootstrap/future_ideas.md`: Future enhancement ideas
- `examples/`: Example projects (e.g., city-explorer)
- `implementation/`: Phase-by-phase implementation plans