# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

intentc is a "Compiler of Intent" - a tool that transforms loosely specified intents into precise code using AI coding agents, inspired by GNU Make's declarative approach to build management.

## Project Status

Currently in bootstrap phase - specifications exist in `bootstrap/` directory but no implementation yet.

## Architecture Overview

- **Language**: Go (chosen for simplicity, speed, and good standard library)
- **Core Concept**: Transform intent files (`.ic`) into working code using AI agents
- **Validation**: Built-in validation system using `.icv` files
- **State Management**: Git-based with append-only commit log

## Key Commands (Planned)

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

## Important Implementation Notes

- Git is a prerequisite (tool won't work without it)
- Separate commit prefixes: `intent:` for intents, `generated:` for code
- Feature dependencies form a DAG
- Validation types: FileCheck, FolderCheck, WebCheck, ProjectCheck, CommandLineCheck
- Default to using user's Claude Code auth for API calls

## Key Directories

- `bootstrap/`: Contains product specifications and future ideas
- `bootstrap/product_specs.md`: Detailed product specifications
- `bootstrap/future_ideas.md`: Future enhancement ideas