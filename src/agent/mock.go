package agent

import (
	"context"

	"github.com/pboueri/intentc/src"
)

type MockAgent struct {
	Name          string
	BuildFunc     func(ctx context.Context, buildCtx BuildContext) ([]string, error)
	RefineFunc    func(ctx context.Context, target *src.Target, prompt string) error
	ValidateFunc  func(ctx context.Context, validation *src.Validation, generatedFiles []string) (bool, string, error)
}

func NewMockAgent(name string) *MockAgent {
	return &MockAgent{
		Name: name,
	}
}

func (m *MockAgent) Build(ctx context.Context, buildCtx BuildContext) ([]string, error) {
	if m.BuildFunc != nil {
		return m.BuildFunc(ctx, buildCtx)
	}
	
	// Default implementation
	return []string{"mock-file.go"}, nil
}

func (m *MockAgent) Refine(ctx context.Context, target *src.Target, prompt string) error {
	if m.RefineFunc != nil {
		return m.RefineFunc(ctx, target, prompt)
	}
	return nil
}

func (m *MockAgent) Validate(ctx context.Context, validation *src.Validation, generatedFiles []string) (bool, string, error) {
	if m.ValidateFunc != nil {
		return m.ValidateFunc(ctx, validation, generatedFiles)
	}
	return true, "Mock validation passed", nil
}

func (m *MockAgent) GetName() string {
	return m.Name
}

func (m *MockAgent) GetType() string {
	return "mock"
}

type MockAgentFactory struct{}

func NewMockAgentFactory() *MockAgentFactory {
	return &MockAgentFactory{}
}

func (f *MockAgentFactory) CreateAgent(config src.Agent) (Agent, error) {
	return NewMockAgent("mock-agent"), nil
}

func (f *MockAgentFactory) GetSupportedTypes() []string {
	return []string{"mock"}
}
