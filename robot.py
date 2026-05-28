"""Reachy Mini singleton management."""

import logging
from typing import Optional

from reachy_mini import ReachyMini

logger = logging.getLogger(__name__)

_shared_robot: Optional[ReachyMini] = None


def get_shared_robot() -> ReachyMini:
    """Get or create the shared Reachy Mini instance."""
    global _shared_robot
    if _shared_robot is None:
        _shared_robot = ReachyMini()
        _shared_robot.media.start_recording()
        _shared_robot.media.start_playing()
        logger.info("[ROBOT] Shared Reachy robot initialized with recording and playing")
    return _shared_robot


def release_shared_robot() -> None:
    """Release the shared robot instance."""
    global _shared_robot
    if _shared_robot is not None:
        _shared_robot = None
        logger.info("[ROBOT] Shared robot released")
