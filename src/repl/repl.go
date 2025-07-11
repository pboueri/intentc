package repl

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/agent"
	"github.com/pboueri/intentc/src/config"
	"github.com/pboueri/intentc/src/git"
	"github.com/pboueri/intentc/src/logger"
	"github.com/pboueri/intentc/src/parser"
	"github.com/pboueri/intentc/src/state"
)

// REPL represents the interactive REPL environment
type REPL struct {
	config       *config.Config
	agent        agent.Agent
	gitManager   git.GitManager
	stateManager state.StateManager
	target       *src.Target
	context      *ReplContext
	reader       *bufio.Reader
	writer       io.Writer
	projectRoot  string
}

// NewREPL creates a new REPL instance
func NewREPL(cfg *config.Config, target string) (*REPL, error) {
	// Get project root
	projectRoot, err := os.Getwd()
	if err != nil {
		return nil, fmt.Errorf("failed to get current directory: %w", err)
	}

	// Initialize dependencies
	gitMgr := git.NewGitManager(projectRoot)
	stateMgr := state.NewGitStateManager(gitMgr, projectRoot)

	// Create agent based on config
	var agentInstance agent.Agent
	switch cfg.Agent.Provider {
	case "claude":
		agentInstance = agent.NewClaudeAgent("claude", agent.ClaudeAgentConfig{
			Timeout:   cfg.Agent.Timeout,
			Retries:   cfg.Agent.Retries,
			RateLimit: cfg.Agent.RateLimit,
			CLIArgs:   cfg.Agent.CLIArgs,
		})
	case "cli":
		agentInstance = agent.NewCLIAgent(agent.CLIAgentConfig{
			Name:      cfg.Agent.Provider,
			Command:   cfg.Agent.Command,
			Args:      cfg.Agent.CLIArgs,
			Timeout:   cfg.Agent.Timeout,
			Retries:   cfg.Agent.Retries,
			RateLimit: cfg.Agent.RateLimit,
		})
	case "mock":
		agentInstance = &agent.MockAgent{}
	default:
		return nil, fmt.Errorf("unknown agent provider: %s", cfg.Agent.Provider)
	}

	// Parse target
	intentParser := parser.NewIntentParser()
	targetPath := filepath.Join(projectRoot, "intent", target, target+".ic")
	intent, err := intentParser.ParseIntent(targetPath)
	if err != nil {
		return nil, fmt.Errorf("failed to parse target intent: %w", err)
	}

	// Parse validations
	validationParser := parser.NewValidationParser()
	validations := []*src.ValidationFile{}
	validationDir := filepath.Join(projectRoot, "intent", target)
	files, _ := filepath.Glob(filepath.Join(validationDir, "*.icv"))
	for _, file := range files {
		val, err := validationParser.ParseValidationFile(file)
		if err != nil {
			logger.Warn("Failed to parse validation %s: %v", file, err)
			continue
		}
		validations = append(validations, val)
	}

	targetObj := &src.Target{
		Name:        target,
		Intent:      intent,
		Validations: validations,
	}

	// Create REPL context
	ctx := NewReplContext(targetObj, projectRoot)

	return &REPL{
		config:       cfg,
		agent:        agentInstance,
		gitManager:   gitMgr,
		stateManager: stateMgr,
		target:       targetObj,
		context:      ctx,
		reader:       bufio.NewReader(os.Stdin),
		writer:       os.Stdout,
		projectRoot:  projectRoot,
	}, nil
}

// Run starts the REPL loop
func (r *REPL) Run(ctx context.Context) error {
	// Show welcome message
	fmt.Fprintln(r.writer, "intentc REPL - Interactive Refinement Mode")
	fmt.Fprintf(r.writer, "Target: %s\n", r.target.Name)
	fmt.Fprintln(r.writer, "Type 'help' for available commands or 'exit' to quit.")
	fmt.Fprintln(r.writer)

	// Load initial state
	if err := r.loadInitialState(ctx); err != nil {
		logger.Warn("Failed to load initial state: %v", err)
	}

	// Main REPL loop
	for {
		fmt.Fprint(r.writer, "> ")

		// Read input
		input, err := r.reader.ReadString('\n')
		if err != nil {
			if err == io.EOF {
				fmt.Fprintln(r.writer, "\nExiting...")
				return nil
			}
			return fmt.Errorf("failed to read input: %w", err)
		}

		input = strings.TrimSpace(input)
		if input == "" {
			continue
		}

		// Parse and execute command
		parts := strings.Fields(input)
		if len(parts) == 0 {
			continue
		}

		command := parts[0]
		args := parts[1:]

		if err := r.executeCommand(ctx, command, args); err != nil {
			if err.Error() == "exit" {
				fmt.Fprintln(r.writer, "Exiting...")
				return nil
			}
			fmt.Fprintf(r.writer, "Error: %v\n", err)
		}
	}
}

// executeCommand executes a REPL command
func (r *REPL) executeCommand(ctx context.Context, command string, args []string) error {
	switch command {
	case "help", "h", "?":
		return r.cmdHelp()
	case "show", "s":
		return r.cmdShow(args)
	case "edit", "e":
		return r.cmdEdit(args)
	case "refine", "r":
		return r.cmdRefine(ctx, args)
	case "validate", "v":
		return r.cmdValidate(ctx)
	case "diff", "d":
		return r.cmdDiff(ctx)
	case "commit", "c":
		return r.cmdCommit(ctx, args)
	case "rollback":
		return r.cmdRollback(ctx)
	case "history":
		return r.cmdHistory()
	case "exit", "quit", "q":
		return fmt.Errorf("exit")
	default:
		return fmt.Errorf("unknown command: %s (type 'help' for available commands)", command)
	}
}

// cmdHelp shows available commands
func (r *REPL) cmdHelp() error {
	help := `Available commands:
  help, h, ?         - Show this help message
  show [file]        - Display current generation or specific file
  edit <file>        - Display file for discussion
  refine <prompt>    - Request changes to the generated code
  validate           - Run validations on current code
  diff               - Show changes since last commit
  commit <message>   - Commit current changes
  rollback           - Undo last refinement
  history            - Show refinement history
  exit, quit, q      - Exit REPL

Shortcuts:
  s = show, e = edit, r = refine, v = validate, d = diff, c = commit, q = quit
`
	fmt.Fprint(r.writer, help)
	return nil
}

// cmdShow displays files
func (r *REPL) cmdShow(args []string) error {
	if len(args) == 0 {
		// Show all generated files
		files := r.context.GetGeneratedFiles()
		if len(files) == 0 {
			fmt.Fprintln(r.writer, "No generated files yet.")
			return nil
		}
		fmt.Fprintln(r.writer, "Generated files:")
		for _, file := range files {
			fmt.Fprintf(r.writer, "  - %s\n", file)
		}
		return nil
	}

	// Show specific file
	file := args[0]
	content, err := os.ReadFile(file)
	if err != nil {
		return fmt.Errorf("failed to read file %s: %w", file, err)
	}

	fmt.Fprintf(r.writer, "\n=== %s ===\n", file)
	fmt.Fprintln(r.writer, string(content))
	fmt.Fprintln(r.writer, "=== END ===")
	return nil
}

// cmdEdit displays a file for discussion
func (r *REPL) cmdEdit(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: edit <file>")
	}

	file := args[0]
	content, err := os.ReadFile(file)
	if err != nil {
		return fmt.Errorf("failed to read file %s: %w", file, err)
	}

	// Add to context for discussion
	r.context.AddFileContext(file, string(content))

	fmt.Fprintf(r.writer, "\n=== %s ===\n", file)
	fmt.Fprintln(r.writer, string(content))
	fmt.Fprintln(r.writer, "=== END ===")
	fmt.Fprintf(r.writer, "File '%s' added to refinement context.\n", file)
	return nil
}

// cmdRefine requests changes from the agent
func (r *REPL) cmdRefine(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: refine <prompt>")
	}

	prompt := strings.Join(args, " ")
	r.context.AddRefinement(prompt, "")

	fmt.Fprintln(r.writer, "Refining with agent...")

	// Prepare refinement prompt with context
	fullPrompt := r.context.BuildRefinementPrompt(prompt)

	// Execute refinement
	if err := r.agent.Refine(ctx, r.target, fullPrompt); err != nil {
		return fmt.Errorf("refinement failed: %w", err)
	}

	// Detect changes
	changes, err := r.detectChanges(ctx)
	if err != nil {
		logger.Warn("Failed to detect changes: %v", err)
	} else {
		r.context.UpdateRefinementResponse(prompt, fmt.Sprintf("Modified %d file(s)", len(changes)))
		if len(changes) > 0 {
			fmt.Fprintf(r.writer, "Modified %d file(s):\n", len(changes))
			for _, file := range changes {
				fmt.Fprintf(r.writer, "  - %s\n", file)
			}
		}
	}

	fmt.Fprintln(r.writer, "Refinement complete.")
	return nil
}

// cmdValidate runs validations
func (r *REPL) cmdValidate(ctx context.Context) error {
	fmt.Fprintln(r.writer, "Running validations...")

	// Get generated files
	files := r.context.GetGeneratedFiles()
	if len(files) == 0 {
		fmt.Fprintln(r.writer, "No generated files to validate.")
		return nil
	}

	// For now, just check if files exist
	// Full validation would require implementing the validation runner
	passed := 0
	failed := 0
	
	if len(r.target.Validations) == 0 {
		fmt.Fprintln(r.writer, "No validations defined for this target.")
		return nil
	}

	for _, vf := range r.target.Validations {
		for _, v := range vf.Validations {
			// Simple file existence check for now
			exists := false
			for _, file := range files {
				if _, err := os.Stat(file); err == nil {
					exists = true
					break
				}
			}
			
			if exists {
				passed++
				fmt.Fprintf(r.writer, "✓ %s: Files exist\n", v.Name)
			} else {
				failed++
				fmt.Fprintf(r.writer, "✗ %s: No files found\n", v.Name)
			}
		}
	}

	fmt.Fprintf(r.writer, "\nValidation Summary: %d passed, %d failed\n", passed, failed)
	return nil
}

// cmdDiff shows changes
func (r *REPL) cmdDiff(ctx context.Context) error {
	status, err := r.gitManager.GetStatus(ctx)
	if err != nil {
		return fmt.Errorf("failed to get git status: %w", err)
	}

	if status.Clean {
		fmt.Fprintln(r.writer, "No changes to show.")
		return nil
	}

	fmt.Fprintln(r.writer, "Changed files:")
	for _, file := range status.ModifiedFiles {
		fmt.Fprintf(r.writer, "  M %s\n", file)
	}
	for _, file := range status.UntrackedFiles {
		fmt.Fprintf(r.writer, "  ? %s\n", file)
	}

	return nil
}

// cmdCommit commits changes
func (r *REPL) cmdCommit(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: commit <message>")
	}

	message := strings.Join(args, " ")

	// Get current status
	status, err := r.gitManager.GetStatus(ctx)
	if err != nil {
		return fmt.Errorf("failed to get git status: %w", err)
	}

	if status.Clean {
		fmt.Fprintln(r.writer, "No changes to commit.")
		return nil
	}

	// Stage all changes
	allFiles := append(status.ModifiedFiles, status.UntrackedFiles...)
	if err := r.gitManager.Add(ctx, allFiles); err != nil {
		return fmt.Errorf("failed to stage files: %w", err)
	}

	// Commit with appropriate prefix
	fullMessage := fmt.Sprintf("refine: %s", message)
	if err := r.gitManager.Commit(ctx, fullMessage); err != nil {
		return fmt.Errorf("failed to commit: %w", err)
	}

	fmt.Fprintf(r.writer, "Committed %d file(s) with message: %s\n", len(allFiles), fullMessage)
	return nil
}

// cmdRollback undoes the last refinement
func (r *REPL) cmdRollback(ctx context.Context) error {
	// Get the last commit
	commits, err := r.gitManager.GetLog(ctx, 2)
	if err != nil {
		return fmt.Errorf("failed to get commit log: %w", err)
	}

	if len(commits) < 2 {
		return fmt.Errorf("no previous commit to rollback to")
	}

	// Check if the last commit is a refinement
	if !strings.HasPrefix(commits[0].Message, "refine:") {
		return fmt.Errorf("last commit is not a refinement")
	}

	// Reset to the previous commit
	if err := r.gitManager.CheckoutCommit(ctx, commits[1].Hash); err != nil {
		return fmt.Errorf("failed to rollback: %w", err)
	}

	fmt.Fprintf(r.writer, "Rolled back refinement: %s\n", commits[0].Message)
	return nil
}

// cmdHistory shows refinement history
func (r *REPL) cmdHistory() error {
	history := r.context.GetHistory()
	if len(history) == 0 {
		fmt.Fprintln(r.writer, "No refinement history.")
		return nil
	}

	fmt.Fprintln(r.writer, "Refinement History:")
	for i, entry := range history {
		fmt.Fprintf(r.writer, "\n[%d] %s\n", i+1, entry.Timestamp.Format("2006-01-02 15:04:05"))
		fmt.Fprintf(r.writer, "Prompt: %s\n", entry.Prompt)
		if entry.Response != "" {
			fmt.Fprintf(r.writer, "Result: %s\n", entry.Response)
		}
	}
	return nil
}

// loadInitialState loads the current state of the target
func (r *REPL) loadInitialState(ctx context.Context) error {
	// Get the latest build result
	result, err := r.stateManager.GetLatestBuildResult(ctx, r.target.Name)
	if err != nil {
		return fmt.Errorf("no previous build found for target '%s'", r.target.Name)
	}

	// Set generated files in context
	for _, file := range result.Files {
		r.context.AddGeneratedFile(file)
	}

	logger.Debug("Loaded %d generated files from previous build", len(result.Files))
	return nil
}

// detectChanges detects what files have changed
func (r *REPL) detectChanges(ctx context.Context) ([]string, error) {
	status, err := r.gitManager.GetStatus(ctx)
	if err != nil {
		return nil, err
	}

	changes := append(status.ModifiedFiles, status.UntrackedFiles...)
	
	// Update context with new/modified files
	for _, file := range changes {
		r.context.AddGeneratedFile(file)
	}

	return changes, nil
}