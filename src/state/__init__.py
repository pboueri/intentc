"""State management package - tracks build state persistently."""

from state.manager import FileStateManager, new_state_manager

__all__ = [
    "FileStateManager",
    "new_state_manager",
]
