# CLI Agent Documentation

## Overview

The CLI Agent is a generic implementation that allows intentc to use any command-line tool as a coding agent. This provides flexibility to integrate with various AI tools like Claude, GPT-4, or custom implementations.

## Architecture

### Inheritance Structure

```
Agent (interface)
  │
  ├── CLIAgent (generic implementation)
  │     │
  │     └── ClaudeAgent (inherits from CLIAgent)
  │
  └── MockAgent (for testing)
```

### Key Components

1. **CLIAgent** (`src/agent/cli.go`)
   - Generic implementation that can execute any CLI command
   - Handles stdin/stdout communication with the command
   - Parses output to identify generated files
   - Supports retries, timeouts, and rate limiting

2. **ClaudeAgent** (`src/agent/claude.go`)
   - Inherits from CLIAgent using Go composition
   - Overrides specific methods for Claude-specific behavior
   - Uses the same prompt templates but can customize them

3. **PromptTemplates** (`src/agent/templates.go`)
   - Externalized, readable prompt templates
   - Unified templates used by both CLI and Claude agents
   - Supports Go text/template syntax

## Configuration

### Using Claude (default)

```yaml
agent:
  provider: claude
  timeout: 5m
  retries: 3
  rate_limit: 1s
```

### Using a generic CLI agent

```yaml
agent:
  provider: cli
  command: "amp"
  cli_args: ["--model", "best"]
  timeout: 10m
  retries: 3
  rate_limit: 2s
```

### Using a custom command without explicit provider

```yaml
agent:
  command: "my-ai-tool"
  cli_args: ["--json", "--quiet"]
```

## Implementation Details

### CLIAgent Methods

- `Build(ctx, buildCtx)` - Generates code based on intent
- `Refine(ctx, target, prompt)` - Refines existing implementation
- `Validate(ctx, validation, files)` - Validates generated code
- `SetTemplates(templates)` - Customizes prompt templates

### File Parsing

The CLI agent parses output looking for patterns like:
- `Created file: path/to/file`
- `Generated: path/to/file`
- `Wrote path/to/file`
- `Modified path/to/file`

### Template Data

Templates receive data including:
- Project root path
- Generation ID
- Intent name and content
- Dependencies
- Validations
- Generated files

## Examples

### Custom AI Tool Integration

```go
config := agent.CLIAgentConfig{
    Name:      "gpt4-agent",
    Command:   "openai",
    Args:      []string{"--model", "gpt-4"},
    Timeout:   10 * time.Minute,
    Retries:   3,
    RateLimit: 2 * time.Second,
}
agent := agent.NewCLIAgent(config)
```

### Template Customization

```go
customTemplates := agent.PromptTemplates{
    Build:    "Generate code for: {{.IntentName}}\n{{.IntentContent}}",
    Refine:   "Refine {{.TargetName}}: {{.UserFeedback}}",
    Validate: "Check {{.ValidationName}}: {{.ValidationDescription}}",
}
agent.SetTemplates(customTemplates)
```

## Testing

The implementation includes comprehensive tests:
- Unit tests for CLIAgent functionality
- Integration tests with real commands
- Factory pattern tests
- Template usage tests
- Inheritance verification tests

## Future Enhancements

1. Support for streaming output
2. Better error handling for specific CLI tools
3. Plugin system for custom parsers
4. Configuration profiles for common AI tools