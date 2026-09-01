from __future__ import annotations

from . import episode_assignment_strict as _strict
from .episode_assignment_strict import *  # noqa: F403
from .mixed_episode_assignment import (
    assign_episode_group,
    assign_episode_group_with_provider,
)


def __getattr__(name: str):
    """Preserve access to strict internal helpers for compatibility."""

    return getattr(_strict, name)


__all__ = [
    "AssignmentStatus",
    "EpisodeGroupAssignment",
    "SourceEpisodeAssignment",
    "SourceEpisodeInput",
    "assign_episode_group",
    "assign_episode_group_with_provider",
]
