"""Standalone agent package for use outside the main intentc package.

This is a mirror of intentc.build.agents with identical defaults and behavior.
"""

from agent.base import Agent
from agent.claude_agent import ClaudeAgent
from agent.cli_agent import CLIAgent
from agent.factory import create_from_profile
from agent.mock_agent import MockAgent, MockCall
from agent.models import (
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
