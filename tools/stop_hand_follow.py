"""tools.stop_hand_follow — 停止手部跟随(P6 实装)。"""
from __future__ import annotations

import logging
from typing import Any

from tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)


class StopHandFollowTool(Tool):
    """Disable the camera-based hand-following behavior."""

    name = "stop_hand_follow"
    description = (
        "Stop following the user's hand (head stays at last pose). "
        "用中文说「停止手部跟随」「别跟着我的手了」「停止跟随」等关闭此功能。"
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        hf = getattr(deps, "hand_follower", None)
        if hf is None:
            return {"status": "unavailable", "message": "HandFollower 未注册", "stage": "P6"}

        try:
            hf.disable()
        except Exception as e:
            logger.exception("stop_hand_follow 失败")
            return {"error": f"{type(e).__name__}: {e}"}

        return {"status": "stopped", "stage": "P6"}
