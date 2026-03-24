"""Factory function for creating agents from profiles (standalone copy)."""

from __future__ import annotations

from agent.base import Agent
from agent.claude_agent import ClaudeAgent
from agent.cli_agent import CLIAgent
from agent.models import AgentError, AgentProfile, LogFn


def create_from_profile(profile: AgentProfile, log: LogFn | None = None) -> Agent:
    """Create an Agent instance from an AgentProfile."""
    if profile.provider == "claude":
        return ClaudeAgent(profile=profile, log=log)
    elif profile.provider == "cli":
        return CLIAgent(profile=profile, log=log)
    else:
        raise AgentError(f"Unknown agent provider: {profile.provider!r}")
