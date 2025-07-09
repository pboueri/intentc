# intentc

[A Compiler of Intent](https://pboueri.github.io/blog/compilers-of-intent/)

Transform loosely specified intents into precise code using AI coding agents. Inspired by GNU Make's declarative approach, intentc uses markdown-based intent files (`.ic`) to generate working code through Claude or any CLI-based coding tool.

## Key Commands

```bash
# Initialize a new intentc project
intentc init

# Build targets from intent files
intentc build <target>

# Clean generated files
intentc clean <target>

# Validate outputs against constraints
intentc validate <target>

# Show target status
intentc status

# Interactive refinement REPL
intentc refine

# Configure agents and models
intentc config
```

## Quick Start

1. Install intentc: `go install github.com/pboueri/intentc`
2. Initialize your project: `intentc init`
3. Write intent files (`.ic`) describing what you want to build
4. Build your targets: `intentc build <target>`
5. Validate the output: `intentc validate <target>`