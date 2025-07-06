package cmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"
	"github.com/pboueri/intentc/src/git"
	"github.com/pboueri/intentc/src"
	"gopkg.in/yaml.v3"
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

	gitMgr := git.NewGitManager(cwd)
	isGitRepo, err := gitMgr.IsGitRepo(ctx, cwd)
	if err != nil {
		return fmt.Errorf("failed to check git repository: %w", err)
	}
	if !isGitRepo {
		return fmt.Errorf("intentc requires a git repository. Please run 'git init' first")
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

	defaultConfig := src.ProjectConfig{
		Version:      "1.0",
		DefaultAgent: "claude-code",
		Agents: map[string]src.Agent{
			"claude-code": {
				Type:    "claude-code",
				Command: "claude-code",
				Parameters: map[string]string{
					"model": "claude-3-5-sonnet-20241022",
				},
			},
		},
		Settings: map[string]string{
			"auto_validate": "true",
			"verbose":       "false",
		},
	}

	configData, err := yaml.Marshal(&defaultConfig)
	if err != nil {
		return fmt.Errorf("failed to marshal config: %w", err)
	}

	configFile := filepath.Join(cwd, ".intentc")
	if err := os.WriteFile(configFile, configData, 0644); err != nil {
		return fmt.Errorf("failed to create config file: %w", err)
	}

	fmt.Println("✓ Initialized intentc project structure")
	fmt.Println("✓ Created intent directory")
	fmt.Println("✓ Created example feature")
	fmt.Println("✓ Created .intentc configuration file")
	fmt.Println("\nNext steps:")
	fmt.Println("1. Edit intent/project.ic to define your project's overall intent")
	fmt.Println("2. Create feature folders in intent/ for each feature")
	fmt.Println("3. Run 'intentc build' to generate code from your intents")

	return nil
}
