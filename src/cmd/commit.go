package cmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/pboueri/intentc/src/config"
	"github.com/pboueri/intentc/src/logger"
	"github.com/spf13/cobra"
)

var (
	commitMessage string
	commitAll     bool
)

var commitCmd = &cobra.Command{
	Use:   "commit",
	Short: "Commit changes",
	Long: `Commit both intent and generated code changes to git with appropriate prefixes.

The commit command automatically separates intent files (.ic, .icv) from generated code
and creates separate commits with appropriate prefixes:
  - "intent:" for intent and validation files
  - "generated:" for AI-generated code

If both types of files are present, two commits will be created when using --all flag.
Note: If files are already staged together, they will be committed together with the
appropriate prefix based on the file types present.`,
	RunE: runCommit,
}

func init() {
	commitCmd.Flags().StringVarP(&commitMessage, "message", "m", "", "Commit message (required)")
	commitCmd.Flags().BoolVarP(&commitAll, "all", "a", false, "Automatically stage all changes")
	commitCmd.MarkFlagRequired("message")
}

func runCommit(cmd *cobra.Command, args []string) error {
	ctx := context.Background()

	// Get project root
	projectRoot, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("failed to get current directory: %w", err)
	}

	// Load configuration (might be needed in the future)
	_, err = config.LoadConfig(projectRoot)
	if err != nil {
		return fmt.Errorf("failed to load config: %w", err)
	}

	// Create appropriate managers based on git availability
	gitMgr, _, err := CreateManagers(ctx, projectRoot)
	if err != nil {
		return fmt.Errorf("failed to initialize managers: %w", err)
	}

	// Check if it's a git repo (no-op git manager will return false)
	isRepo, err := gitMgr.IsGitRepo(ctx, projectRoot)
	if err != nil {
		return fmt.Errorf("failed to check git status: %w", err)
	}
	if !isRepo {
		logger.Info("No git repository detected - commit operation will be skipped")
		return nil
	}

	// Get git status
	status, err := gitMgr.GetStatus(ctx)
	if err != nil {
		return fmt.Errorf("failed to get git status: %w", err)
	}

	// Collect all files to process
	var filesToProcess []string
	
	if commitAll {
		// If --all flag is set, process all changes
		filesToProcess = append(filesToProcess, status.ModifiedFiles...)
		filesToProcess = append(filesToProcess, status.UntrackedFiles...)
		// Also include already staged files
		filesToProcess = append(filesToProcess, status.StagedFiles...)
	} else {
		// Otherwise, only process staged files
		// BUT we need to remember them because git will unstage after first commit
		filesToProcess = status.StagedFiles
	}

	// Check if there are any files to commit
	if len(filesToProcess) == 0 {
		logger.Info("No changes to commit")
		if !commitAll && (len(status.ModifiedFiles) > 0 || len(status.UntrackedFiles) > 0) {
			logger.Info("Use 'git add <file>...' to stage changes or use --all flag")
		}
		return nil
	}

	// Separate intent files from generated files
	var intentFiles []string
	var generatedFiles []string

	for _, file := range filesToProcess {
		if isIntentFile(file) {
			intentFiles = append(intentFiles, file)
		} else {
			generatedFiles = append(generatedFiles, file)
		}
	}

	// Get state manager from CreateManagers (we already have gitMgr)
	_, stateMgr, err := CreateManagers(ctx, projectRoot)
	if err != nil {
		return fmt.Errorf("failed to get state manager: %w", err)
	}
	if err := stateMgr.Initialize(ctx); err != nil {
		return fmt.Errorf("failed to initialize state manager: %w", err)
	}

	// Commit intent files first
	if len(intentFiles) > 0 {
		// Stage intent files (even if already staged, since we may have multiple commits)
		if err := gitMgr.Add(ctx, intentFiles); err != nil {
			return fmt.Errorf("failed to stage intent files: %w", err)
		}

		// Create intent commit
		intentMsg := fmt.Sprintf("intent: %s", commitMessage)
		if err := gitMgr.Commit(ctx, intentMsg); err != nil {
			return fmt.Errorf("failed to commit intent files: %w", err)
		}

		logger.Info("Committed %d intent file(s) with message: %s", len(intentFiles), intentMsg)
		for _, file := range intentFiles {
			logger.Debug("  - %s", file)
		}
	}

	// Commit generated files
	if len(generatedFiles) > 0 {
		// Check if there are actually files to commit
		// (they might have been included in the intent commit if staged together)
		statusCheck, err := gitMgr.GetStatus(ctx)
		if err != nil {
			return fmt.Errorf("failed to check git status: %w", err)
		}
		
		// Check if any of our generated files are still uncommitted
		uncommittedGenerated := false
		for _, file := range generatedFiles {
			// Check staged files too
			for _, stagedFile := range statusCheck.StagedFiles {
				if stagedFile == file {
					uncommittedGenerated = true
					break
				}
			}
			if !uncommittedGenerated {
				for _, modFile := range statusCheck.ModifiedFiles {
					if modFile == file {
						uncommittedGenerated = true
						break
					}
				}
			}
			if !uncommittedGenerated {
				for _, untrFile := range statusCheck.UntrackedFiles {
					if untrFile == file {
						uncommittedGenerated = true
						break
					}
				}
			}
		}
		
		if uncommittedGenerated {
			// Stage generated files
			if err := gitMgr.Add(ctx, generatedFiles); err != nil {
				return fmt.Errorf("failed to stage generated files: %w", err)
			}

			// Get the latest generation ID for any built target
			var generationID string
			// This is a simplified approach - in a real implementation, we might want to
			// track which files belong to which target/generation
			targets := findTargetsFromFiles(generatedFiles)
			if len(targets) > 0 {
				for _, target := range targets {
					result, err := stateMgr.GetLatestBuildResult(ctx, target)
					if err == nil && result != nil {
						generationID = result.GenerationID
						break
					}
				}
			}

			// Create generated commit
			generatedMsg := fmt.Sprintf("generated: %s", commitMessage)
			if generationID != "" {
				generatedMsg = fmt.Sprintf("generated: %s (generation: %s)", commitMessage, generationID)
			}

			if err := gitMgr.Commit(ctx, generatedMsg); err != nil {
				return fmt.Errorf("failed to commit generated files: %w", err)
			}

			logger.Info("Committed %d generated file(s) with message: %s", len(generatedFiles), generatedMsg)
			for _, file := range generatedFiles {
				logger.Debug("  - %s", file)
			}
		} else {
			logger.Debug("Generated files were already committed with intent files")
		}
	}

	// Show final status
	finalStatus, err := gitMgr.GetStatus(ctx)
	if err == nil && finalStatus.Clean {
		logger.Info("Working tree clean")
	}

	return nil
}

// isIntentFile checks if a file is an intent or validation file
func isIntentFile(path string) bool {
	ext := filepath.Ext(path)
	return ext == ".ic" || ext == ".icv"
}

// findTargetsFromFiles attempts to identify which targets the files belong to
// This is a simplified implementation - could be enhanced with better heuristics
func findTargetsFromFiles(files []string) []string {
	targetMap := make(map[string]bool)
	
	for _, file := range files {
		// Simple heuristic: if file is in a directory, use the directory name as target
		dir := filepath.Dir(file)
		if dir != "." && dir != "/" {
			parts := strings.Split(dir, string(filepath.Separator))
			if len(parts) > 0 {
				targetMap[parts[0]] = true
			}
		}
	}

	var targets []string
	for target := range targetMap {
		targets = append(targets, target)
	}
	return targets
}
