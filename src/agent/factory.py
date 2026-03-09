"""Factory for creating agents from an AgentProfile."""

from __future__ import annotations

from core.types import AgentProfile

from .base import Agent
from .claude_agent import ClaudeAgent
from .cli_agent import CLIAgent
from .codex_agent import CodexAgent


def create_from_profile(profile: AgentProfile) -> Agent:
    """Instantiate the appropriate agent based on ``profile.provider``.

    Supported providers:
    - ``"claude"`` -> :class:`ClaudeAgent`
    - ``"codex"``  -> :class:`CodexAgent`
    - ``"cli"``    -> :class:`CLIAgent`

    Raises:
        ValueError: If the provider string is not recognised.
    """
    provider = profile.provider.lower()

    if provider == "claude":
        return ClaudeAgent(profile)  # type: ignore[return-value]
    if provider == "codex":
        return CodexAgent(profile)  # type: ignore[return-value]
    if provider == "cli":
        return CLIAgent(profile)  # type: ignore[return-value]

    raise ValueError(
        f"Unknown agent provider: {profile.provider!r}. "
        f"Supported providers: claude, codex, cli"
    )
