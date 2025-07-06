package agent

import (
	"context"
	"fmt"
	
	"github.com/pboueri/intentc/src"
)

type BuildContext struct {
	Intent       *src.Intent
	Validations  []*src.ValidationFile
	ProjectRoot  string
	GenerationID string
}

type Agent interface {
	Build(ctx context.Context, buildCtx BuildContext) ([]string, error)
	Refine(ctx context.Context, target *src.Target, prompt string) error
	Validate(ctx context.Context, validation *src.Validation, generatedFiles []string) (bool, string, error)
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
