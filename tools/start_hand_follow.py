"""tools.start_hand_follow — 开启手部跟随(P6 实装)。

P0.4 占位 → P6 真接 HandFollower。
"""
from __future__ import annotations

import logging
from typing import Any

from tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)


class StartHandFollowTool(Tool):
    """Enable the camera-based hand-following behavior (P6+)."""

    name = "start_hand_follow"
    description = (
        "Start following the user's hand with the camera. "
        "Robot's head will track the detected palm. Default disabled."
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
            return {
                "status": "unavailable",
                "message": "HandFollower 没注册到 deps(可能没装 mediapipe / 模型没下)",
                "stage": "P6",
            }

        try:
            hf.enable()
        except Exception as e:
            logger.exception("start_hand_follow 失败")
            return {"error": f"{type(e).__name__}: {e}"}

        return {
            "status": "started",
            "available": hf.available,
            "stage": "P6",
        }
