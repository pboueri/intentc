package main

import (
	"os"
	"path/filepath"

	"github.com/pboueri/intentc/src/cmd"
	"github.com/pboueri/intentc/src/config"
	"github.com/pboueri/intentc/src/logger"
)

func main() {
	// Initialize logger with defaults first
	logger.Initialize()

	// Try to load config and reinitialize logger if project is initialized
	projectRoot, err := os.Getwd()
	if err == nil {
		if _, err := os.Stat(filepath.Join(projectRoot, ".intentc")); err == nil {
			// Project is initialized, load config
			if cfg, err := config.LoadConfig(projectRoot); err == nil {
				// Reinitialize logger with config
				if err := config.InitializeLogger(cfg, projectRoot); err != nil {
					logger.Warn("Failed to initialize logger from config: %v", err)
				}
			}
		}
	}

	if err := cmd.Execute(); err != nil {
		logger.Error("Command failed: %v", err)
		os.Exit(1)
	}
}