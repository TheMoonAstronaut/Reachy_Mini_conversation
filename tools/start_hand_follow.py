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
        "Start following the user's hand with the camera: the robot's head "
        "tracks the detected palm in real time. "
        "用中文说「开始手部跟随」「跟着我手」「看着我的手」「跟我挥手」等"
        "开启此功能;需要真机已连接且手掌在其摄像头前。默认关闭,"
        "用完应调 stop_hand_follow 关闭。"
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
            "message": (
                "已开启手部跟随。提醒用户:把手掌举到机器人摄像头前,"
                "头部会实时跟随;说「停止手部跟随」可关闭。"
            ),
            "stage": "P6",
        }
