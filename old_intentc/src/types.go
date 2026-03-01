package src

import (
	"time"
)

type Intent struct {
	Name         string   `yaml:"name"`
	Dependencies []string `yaml:"dependencies,omitempty"`
	Content      string   `yaml:"content"`
	FilePath     string   `yaml:"-"`
}

type ValidationFile struct {
	FilePath    string       `yaml:"-"`
	Validations []Validation `yaml:"validations"`
}

type Validation struct {
	Name        string                 `yaml:"name"`
	Type        ValidationType         `yaml:"type"`
	Description string                 `yaml:"description"`
	Parameters  map[string]interface{} `yaml:"parameters"`
	Hidden      bool                   `yaml:"hidden,omitempty"`
}

type ValidationType string

const (
	ValidationTypeFileCheck        ValidationType = "FileCheck"
	ValidationTypeFolderCheck      ValidationType = "FolderCheck"
	ValidationTypeWebCheck         ValidationType = "WebCheck"
	ValidationTypeProjectCheck     ValidationType = "ProjectCheck"
	ValidationTypeCommandLineCheck ValidationType = "CommandLineCheck"
)

type Target struct {
	Name          string
	Intent        *Intent
	Validations   []*ValidationFile
	Dependencies  []*Target
	GenerationID  string
	Status        TargetStatus
	LastGenerated time.Time
}

type TargetStatus string

const (
	TargetStatusPending   TargetStatus = "pending"
	TargetStatusBuilding  TargetStatus = "building"
	TargetStatusBuilt     TargetStatus = "built"
	TargetStatusFailed    TargetStatus = "failed"
	TargetStatusOutdated  TargetStatus = "outdated"
)

type BuildResult struct {
	Target       string
	GenerationID string
	Success      bool
	Error        error
	GeneratedAt  time.Time
	Files        []string
	BuildName    string   // Name of the build directory used
	BuildPath    string   // Full path to the build directory
}

type ProjectConfig struct {
	Version      string              `yaml:"version"`
	DefaultAgent string              `yaml:"default_agent"`
	Agents       map[string]Agent    `yaml:"agents"`
	Settings     map[string]string   `yaml:"settings"`
}

type Agent struct {
	Name   string                 `yaml:"name"`
	Type   string                 `yaml:"type"`
	Config map[string]interface{} `yaml:"config,omitempty"`
}
