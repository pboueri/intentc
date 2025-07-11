package agent

import (
	"context"
	"fmt"
	
	"github.com/pboueri/intentc/src"
	"github.com/pboueri/intentc/src/git"
)

type BuildContext struct {
	Intent       *src.Intent
	Validations  []*src.ValidationFile
	ProjectRoot  string
	GenerationID string
	GitManager   git.GitManager
}

// DecompileContext contains the context for decompiling a codebase
type DecompileContext struct {
	SourcePath   string // Path to source code to analyze
	OutputPath   string // Path where intent files should be created
	ProjectRoot  string // Project root for the decompile operation
}

type Agent interface {
	Build(ctx context.Context, buildCtx BuildContext) ([]string, error)
	Refine(ctx context.Context, target *src.Target, prompt string) error
	Validate(ctx context.Context, validation *src.Validation, generatedFiles []string) (bool, string, error)
	Decompile(ctx context.Context, decompileCtx DecompileContext) ([]string, error)
	GetName() string
	GetType() string
}

type AgentFactory interface {
	CreateAgent(config src.Agent) (Agent, error)
	GetSupportedTypes() []string
}

type AgentRegistry struct {
	factories map[string]AgentFactory
}

func NewAgentRegistry() *AgentRegistry {
	return &AgentRegistry{
		factories: make(map[string]AgentFactory),
	}
}

func (r *AgentRegistry) RegisterFactory(agentType string, factory AgentFactory) {
	r.factories[agentType] = factory
}

func (r *AgentRegistry) CreateAgent(agentType string, config src.Agent) (Agent, error) {
	factory, exists := r.factories[agentType]
	if !exists {
		return nil, fmt.Errorf("unknown agent type: %s", agentType)
	}
	return factory.CreateAgent(config)
}
