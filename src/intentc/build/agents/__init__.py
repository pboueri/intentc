"""Agent module for intentc build system."""

from intentc.build.agents.base import Agent
from intentc.build.agents.claude_agent import ClaudeAgent
from intentc.build.agents.cli_agent import CLIAgent
from intentc.build.agents.factory import create_from_profile
from intentc.build.agents.mock_agent import MockAgent, MockCall
from intentc.build.agents.models import (
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
    DifferencingContext,
    DifferencingResponse,
    DimensionResult,
    LogFn,
    PromptTemplates,
    ValidationResponse,
    load_default_prompts,
    render_differencing_prompt,
    render_prompt,
)

__all__ = [
    "Agent",
    "AgentError",
    "AgentProfile",
    "BuildContext",
    "BuildResponse",
    "CLIAgent",
    "ClaudeAgent",
    "DifferencingContext",
    "DifferencingResponse",
    "DimensionResult",
    "LogFn",
    "MockAgent",
    "MockCall",
    "PromptTemplates",
    "ValidationResponse",
    "create_from_profile",
    "load_default_prompts",
    "render_differencing_prompt",
    "render_prompt",
]
