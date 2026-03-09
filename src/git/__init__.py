"""Git package for intentc - wraps git CLI operations."""

from git.manager import (
    GENERATED_PREFIX,
    INTENT_PREFIX,
    REFINE_PREFIX,
    GitCLIManager,
    GitManager,
    GitStatus,
    new_git_manager,
)

__all__ = [
    "GitStatus",
    "GitManager",
    "GitCLIManager",
    "new_git_manager",
    "INTENT_PREFIX",
    "GENERATED_PREFIX",
    "REFINE_PREFIX",
]
