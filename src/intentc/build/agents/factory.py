"""Factory function for creating agents from profiles."""

from __future__ import annotations

from intentc.build.agents.base import Agent
from intentc.build.agents.claude_agent import ClaudeAgent
from intentc.build.agents.cli_agent import CLIAgent
from intentc.build.agents.models import AgentError, AgentProfile, LogFn


def create_from_profile(profile: AgentProfile, log: LogFn | None = None) -> Agent:
    """Create an Agent instance from an AgentProfile.

    Args:
        profile: The agent profile specifying provider and configuration.
        log: Optional logging callback.

    Returns:
        An Agent implementation matching the profile's provider.

    Raises:
        AgentError: If the provider is unknown.
    """
    if profile.provider == "claude":
        return ClaudeAgent(profile=profile, log=log)
    elif profile.provider == "cli":
        return CLIAgent(profile=profile, log=log)
    else:
        raise AgentError(f"Unknown agent provider: {profile.provider!r}")
