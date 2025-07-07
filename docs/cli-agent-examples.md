# Generic CLI Agent Examples

The generic CLI agent allows you to use any command-line tool as a coding agent in intentc. This provides flexibility to integrate various AI tools or custom scripts.

## Configuration

### Using Claude (Default - Backwards Compatible)

```yaml
# .intentc/config.yaml
version: 1
agent:
  provider: claude
  timeout: 5m
  retries: 3
  rate_limit: 1s
  cli_args: []
```

### Using a Generic CLI Tool

```yaml
# .intentc/config.yaml
version: 1
agent:
  provider: cli
  command: your-cli-tool
  timeout: 10m
  retries: 2
  rate_limit: 2s
  cli_args: ["--flag", "value"]
```

### Using Amp as a Coding Agent

```yaml
# .intentc/config.yaml
version: 1
agent:
  provider: cli
  command: amp
  timeout: 10m
  cli_args: ["--mode", "code"]
```

### Using a Custom Script

```yaml
# .intentc/config.yaml
version: 1
agent:
  provider: cli
  command: /path/to/your/custom-agent.sh
  timeout: 15m
  retries: 1
```

## How It Works

1. **Prompt Generation**: intentc creates a prompt containing:
   - Intent content
   - Validation requirements
   - Project context
   - Generation ID

2. **CLI Execution**: The prompt is sent to the CLI tool via stdin

3. **Output Parsing**: The agent parses the output looking for generated files using patterns like:
   - `Created <filepath>`
   - `Generated: <filepath>`
   - `Wrote <filepath>`
   - `Modified <filepath>`

4. **Retry Logic**: If the command fails, it will retry based on configuration

## Creating a Custom CLI Agent

Your custom CLI tool should:

1. **Accept Input**: Read the prompt from stdin
2. **Generate Code**: Create the requested files
3. **Output File Paths**: Print lines indicating created files
4. **Exit Codes**: Return 0 on success, non-zero on failure

### Example Custom Agent Script

```bash
#!/bin/bash
# custom-agent.sh

# Read the entire prompt from stdin
prompt=$(cat)

# Extract intent information (example parsing)
target=$(echo "$prompt" | grep "Target:" | cut -d' ' -f2)

# Generate code based on prompt
echo "Generating code for target: $target"

# Create files
cat > "src/$target.go" << EOF
package main

// Generated code for $target
func main() {
    println("Implementation for $target")
}
EOF

# Output created files
echo "Created src/$target.go"

# Exit successfully
exit 0
```

## Advanced Configuration

### Provider-Specific Settings

You can also specify the command directly without changing the provider:

```yaml
# .intentc/config.yaml
version: 1
agent:
  provider: custom-llm  # Any name
  command: llm-cli      # The actual command to run
  timeout: 5m
  cli_args: ["generate", "--language", "go"]
```

### Environment Variables

The CLI agent runs with the same environment as intentc, so you can use environment variables for configuration:

```bash
export API_KEY="your-key"
export MODEL="gpt-4"
intentc build
```

## Validation Support

The generic CLI agent also supports validation. When validating, it sends a prompt asking the tool to verify if generated code meets requirements. The tool should respond with "PASS" or "FAIL" followed by an explanation.

## Troubleshooting

1. **Command Not Found**: Ensure the command is in your PATH or use an absolute path
2. **Timeout Issues**: Increase the timeout in config for longer operations
3. **Parsing Issues**: Ensure your tool outputs file creation messages in a recognized format
4. **Permission Denied**: Make sure script files have execute permissions (`chmod +x`)