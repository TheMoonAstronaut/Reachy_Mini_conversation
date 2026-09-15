"""tools.move_head — 头部方向工具(P0.4 简化:用 SDK goto_target)。"""
from __future__ import annotations

import logging
from typing import Any, Literal

from reachy_mini.utils import create_head_pose

from tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)

Direction = Literal["left", "right", "up", "down", "front"]


class MoveHead(Tool):
    """移动头部到指定方向(left/right/up/down/front)。"""

    name = "move_head"
    description = "Move your head in a given direction: left, right, up, down or front."
    parameters_schema = {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["left", "right", "up", "down", "front"],
            },
        },
        "required": ["direction"],
    }

    # (pitch, roll, yaw, x, y, z) 单位:度
    DELTAS: dict[str, tuple[int, int, int, int, int, int]] = {
        "left":  (0, 0, 0, 0,  0,  40),
        "right": (0, 0, 0, 0,  0, -40),
        "up":    (0, 0, 0, 0, -30,  0),
        "down":  (0, 0, 0, 0,  30,  0),
        "front": (0, 0, 0, 0,   0,  0),
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        direction_raw = kwargs.get("direction")
        if not isinstance(direction_raw, str):
            return {"error": "direction must be a string"}
        direction: Direction = direction_raw  # type: ignore[assignment]

        logger.info("Tool call: move_head direction=%s", direction)

        deltas = self.DELTAS.get(direction, self.DELTAS["front"])
        target = create_head_pose(*deltas, degrees=True)

        try:
            # P0.4 简化:直接 goto_target(SDK 已覆盖 GotoQueueMove 功能)
            deps.reachy_mini.goto_target(
                head=target,
                duration=deps.motion_duration_s,
            )
            return {"status": f"looking {direction}"}
        except Exception as e:
            logger.error("move_head failed: %s", e)
            return {"error": f"move_head failed: {type(e).__name__}: {e}"}
