# Custom CLI Agent Example

This example demonstrates how to use intentc with a custom CLI agent instead of the default Claude agent.

## Configuration

The `.intentc/config.yaml` file shows how to configure a custom CLI agent:

```yaml
agent:
  provider: cli
  command: "amp"  # Your custom CLI tool
  cli_args: ["--no-interactive"]
  timeout: 10m
  retries: 3
  rate_limit: 2s
```

## Alternative Configurations

### Using a shell script as an agent

```yaml
agent:
  provider: cli
  command: "bash"
  cli_args: ["./my-agent.sh"]
```

### Using another AI tool

```yaml
agent:
  provider: cli
  command: "openai"
  cli_args: ["--model", "gpt-4"]
```

### Backwards compatibility with Claude

```yaml
agent:
  provider: claude  # This uses the ClaudeAgent which inherits from CLIAgent
  timeout: 5m
```

## How it works

1. The generic CLI agent (`src/agent/cli.go`) can execute any command-line tool
2. It passes prompts via stdin to the specified command
3. It parses the output to identify generated files
4. The Claude agent (`src/agent/claude.go`) inherits from CLI agent but adds Claude-specific parsing

## Testing custom agents

You can test your custom agent with:

```bash
# Initialize project
intentc init

# Configure your agent in .intentc/config.yaml

# Build your intents
intentc build my-feature
```