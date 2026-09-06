"""Interactive refinement: an agent-driven coding session, journaled, then
baked back into intent by a second, non-interactive pass."""

from intentc.refine.workflow import (
    RefineOutcome,
    RefineUsageError,
    abandon_refinement,
    bake_refinement,
    run_refine,
)

__all__ = [
    "RefineOutcome",
    "RefineUsageError",
    "abandon_refinement",
    "bake_refinement",
    "run_refine",
]
