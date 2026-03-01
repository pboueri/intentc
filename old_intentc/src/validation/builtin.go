package validation

import (
	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/agent"
)

// RegisterBuiltinValidators registers all built-in validators with the registry
func RegisterBuiltinValidators(registry *ValidatorRegistry, agent agent.Agent) {
	// Register validators that don't need agent
	registry.RegisterValidator(src.ValidationTypeFileCheck, NewFileCheckValidator())
	registry.RegisterValidator(src.ValidationTypeFolderCheck, NewFolderCheckValidator())
	registry.RegisterValidator(src.ValidationTypeCommandLineCheck, NewCommandLineCheckValidator())
	
	// Register validators that need agent
	registry.RegisterValidator(src.ValidationTypeProjectCheck, NewProjectCheckValidator(agent))
	registry.RegisterValidator(src.ValidationTypeWebCheck, NewWebCheckValidator(agent))
}