"""tools.look_at_sound — 声源定位工具(P5 实装)。

P0.4 占位 → P5 真接 SoundLocalizer。
读 state_bus.doa_angle,调 MirrorOrchestrator 转向声源。
"""
from __future__ import annotations

import logging
from typing import Any

from tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)


class LookAtSoundTool(Tool):
    """把头/身体转向当前声源位置(基于 AudioDoA 角度)。"""

    name = "look_at_sound"
    description = (
        "Turn the robot's body toward the current sound source. "
        "Uses AudioDoA backend; needs ReSpeaker hardware (otherwise returns 'N/A')."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """读 state_bus.doa_angle,触发镜像 set_target。"""
        from reachymini_conversation.state_bus import get_state_bus

        bus = get_state_bus()
        angle_deg = bus.get("doa_angle")
        available = bus.get("doa_available", False)

        if not available:
            return {
                "status": "unavailable",
                "message": (
                    "AudioDoA 硬件不可用(纯 sim 模式无 ReSpeaker)"
                    "或初始化失败。UI 显示 N/A。"
                ),
                "stage": "P5",
            }

        if angle_deg is None:
            return {
                "status": "no_data",
                "message": "DoA 还没读到数据(等下一轮 polling)。",
                "stage": "P5",
            }

        # 触发镜像 set_target
        try:
            await deps.reachy_mini.set_target(body_yaw=angle_deg)
            speech = bus.get("doa_speech", False)
            return {
                "status": "looking",
                "target_yaw_deg": round(angle_deg, 1),
                "speech_detected": speech,
            }
        except Exception as e:
            logger.warning(f"look_at_sound 工具失败: {e}")
            return {"error": f"set_target failed: {type(e).__name__}: {e}"}
