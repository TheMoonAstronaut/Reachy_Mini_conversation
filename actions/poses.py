"""Pose definitions for Reachy Mini.

Neutral pose: all joints at 0, antennas at ~10° offset to reduce shaking
Sleep pose: contraction pose from SDK SLEEP_HEAD_JOINT_POSITIONS
"""

from dataclasses import dataclass
from typing import Tuple
import numpy as np

from reachy_mini.utils import create_head_pose


@dataclass
class FullBodyPose:
    """Full body pose with head, antennas, and body yaw."""

    head_pose: np.ndarray
    antennas: Tuple[float, float]
    body_yaw: float


NEUTRAL_HEAD_POSE = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
NEUTRAL_ANTENNAS = (-0.1745, 0.1745)
NEUTRAL_BODY_YAW = 0.0

NEUTRAL_POSE = FullBodyPose(
    head_pose=NEUTRAL_HEAD_POSE,
    antennas=NEUTRAL_ANTENNAS,
    body_yaw=NEUTRAL_BODY_YAW,
)

SLEEP_ANTENNAS = (-3.05, 3.05)
SLEEP_BODY_YAW = 0.0

SLEEP_POSE = FullBodyPose(
    head_pose=NEUTRAL_HEAD_POSE,
    antennas=SLEEP_ANTENNAS,
    body_yaw=SLEEP_BODY_YAW,
)
