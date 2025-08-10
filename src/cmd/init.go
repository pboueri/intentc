package cmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/pboueri/intentc/src/config"
	"github.com/pboueri/intentc/src/git"
	"github.com/pboueri/intentc/src/logger"
	"github.com/spf13/cobra"
)

var initCmd = &cobra.Command{
	Use:   "init",
	Short: "Initialize a new intentc project",
	Long:  `Initialize a new intentc project by creating the intent directory structure and configuration files.`,
	RunE:  runInit,
}

func runInit(cmd *cobra.Command, args []string) error {
	ctx := context.Background()
	cwd, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("failed to get current directory: %w", err)
	}

	// Try to use git, but fall back to no-op if not available
	gitMgr := git.NewGitManager(cwd)
	isGitRepo, err := gitMgr.IsGitRepo(ctx, cwd)
	if err != nil {
		logger.Info("Git not available, using file-based state tracking")
		isGitRepo = false
	}
	
	if !isGitRepo {
		logger.Info("No git repository detected, using file-based state tracking")
	} else {
		logger.Info("Git repository detected, using git-based state tracking")
	}

	intentDir := filepath.Join(cwd, "intent")
	if err := os.MkdirAll(intentDir, 0755); err != nil {
		return fmt.Errorf("failed to create intent directory: %w", err)
	}

	projectSpec := `# Project Intent

This file defines the overall intent of the project.

## Overview

[Describe your project's purpose and goals here]

## Core Principles

[List the core principles that guide this project]

## Architecture

[Describe the high-level architecture]
`
	if err := os.WriteFile(filepath.Join(intentDir, "project.ic"), []byte(projectSpec), 0644); err != nil {
		return fmt.Errorf("failed to create project spec: %w", err)
	}

	exampleDir := filepath.Join(intentDir, "example_feature")
	if err := os.MkdirAll(exampleDir, 0755); err != nil {
		return fmt.Errorf("failed to create example feature directory: %w", err)
	}

	exampleIntent := `# Example Feature

This is an example feature intent.

## Dependencies

Depends On: 

## Intent

[Describe what this feature should do]

## Implementation Notes

[Any specific implementation guidance]
`
	if err := os.WriteFile(filepath.Join(exampleDir, "feature.ic"), []byte(exampleIntent), 0644); err != nil {
		return fmt.Errorf("failed to create example intent: %w", err)
	}

	exampleValidation := `# Example Feature Validations

## File Structure Check

Type: FileCheck

### Parameters
- Path: src/example.go
- Exists: true
- Contains: "package main"

### Description
Ensures the main example file exists and has the correct package declaration.

## Build Check

Type: CommandLineCheck

### Parameters
- Command: go build ./...
- ExpectedExitCode: 0

### Description
Ensures the project builds successfully.
`
	if err := os.WriteFile(filepath.Join(exampleDir, "validations.icv"), []byte(exampleValidation), 0644); err != nil {
		return fmt.Errorf("failed to create example validation: %w", err)
	}

	// Handle existing .intentc file (from old version)
	intentcPath := filepath.Join(cwd, ".intentc")
	if info, err := os.Stat(intentcPath); err == nil && !info.IsDir() {
		// Backup old .intentc file
		backupPath := filepath.Join(cwd, ".intentc.old")
		if err := os.Rename(intentcPath, backupPath); err != nil {
			return fmt.Errorf("failed to backup old .intentc file: %w", err)
		}
		logger.Info("✓ Backed up old .intentc file to .intentc.old")
	}

	// Create .intentc directory
	intentcDir := filepath.Join(cwd, ".intentc")
	if err := os.MkdirAll(intentcDir, 0755); err != nil {
		return fmt.Errorf("failed to create .intentc directory: %w", err)
	}

	// Create default configuration
	defaultConfig := &config.Config{
		Version: 1,
		Agent: config.AgentConfig{
			Provider:  "claude",
			Timeout:   20 * time.Minute,
			Retries:   3,
			RateLimit: 1 * time.Second,
		},
		Build: config.BuildConfig{
			Parallel:     false, // Sequential by default for git state tracking
			CacheEnabled: false,
		},
		Logging: config.LoggingConfig{
			Level: "info",
			Sinks: []config.LogSink{
				{
					Type:     "console",
					Colorize: true,
				},
			},
		},
	}

	if err := config.SaveConfig(cwd, defaultConfig); err != nil {
		return fmt.Errorf("failed to save config: %w", err)
	}

	logger.Info("✓ Initialized intentc project structure")
	logger.Info("✓ Created intent directory")
	logger.Info("✓ Created example feature")
	logger.Info("✓ Created .intentc/config.yaml configuration file")
	logger.Info("\nNext steps:")
	logger.Info("1. Edit intent/project.ic to define your project's overall intent")
	logger.Info("2. Create feature folders in intent/ for each feature")
	logger.Info("3. Ensure you have Claude Code CLI installed and authenticated")
	logger.Info("4. Run 'intentc build' to generate code from your intents")

	return nil
}
