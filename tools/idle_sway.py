"""tools.idle_sway — 待机微动工具(2026-09-16 用户需求)。

背景(source-driven 调查结论):
  - 官方 dances_library 20 个动作全是舞蹈类,无 idle 专用序列
    (源:reachy_mini_dances_library.collection.dance.AVAILABLE_MOVES);
  - 官方 emotions 库 pollen-robotics/reachy-mini-emotions-library(HF)
    本地未缓存、在线查询受网络白名单限制,是否含 idle 动作 UNVERIFIED;
  - 故按 SDK 官方 Move 基类 + create_head_pose 原语自编排"呼吸式"待机序列
    (源:reachy_mini.motion.move.Move / reachy_mini.utils.create_head_pose,
    与 reachy_mini_dances_library.dance_move.DanceMove 同用法)。

设计:幅值刻意小 —— 待机是"我还活着"的呼吸感信号,不是表演。
  一个循环 8s:头部 pitch ±0.03rad 缓慢起伏(呼吸)+ yaw ±0.08rad 超慢
  环顾(16s 周期)+ z 向 4mm 微升降;天线 ±0.15rad 错相轻摆;身体不动
  (省电安静)。工具 repeat 参数控制循环次数。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np
from reachy_mini.motion.move import Move
from reachy_mini.utils import create_head_pose

from tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)

# 待机幅值(弧度/米):小到"活着"但不显眼
_BREATH_PERIOD_S = 8.0   # 呼吸周期
_GAZE_PERIOD_S = 16.0    # 环顾周期(两倍呼吸,避免机械感)
_ANTENNA_PERIOD_S = 5.0  # 天线摆动周期
_PITCH_RAD = 0.03        # ≈1.7° 呼吸点头
_YAW_RAD = 0.08          # ≈4.6° 缓慢环顾
_ROLL_RAD = 0.01         # ≈0.6° 微侧倾
_Z_M = 0.004             # 4mm 升降
_ANTENNA_RAD = 0.15      # ≈8.6° 天线轻摆


class IdleSwayMove(Move):
    """呼吸式待机微动。cycles 个呼吸循环(cycles=1 → 8s)。"""

    def __init__(self, cycles: float = 1.0) -> None:
        self._cycles = max(0.5, float(cycles))

    @property
    def duration(self) -> float:
        return _BREATH_PERIOD_S * self._cycles

    def evaluate(self, t: float):
        breath = 2.0 * np.pi * t / _BREATH_PERIOD_S
        gaze = 2.0 * np.pi * t / _GAZE_PERIOD_S
        antenna_phase = 2.0 * np.pi * t / _ANTENNA_PERIOD_S
        head = create_head_pose(
            x=0.0,
            y=0.0,
            z=_Z_M * float(np.sin(breath)),
            roll=_ROLL_RAD * float(np.sin(breath + np.pi / 4)),
            pitch=_PITCH_RAD * float(np.sin(breath)),
            yaw=_YAW_RAD * float(np.sin(gaze)),
            degrees=False,
            mm=False,
        )
        antennas = np.array(
            [
                _ANTENNA_RAD * float(np.sin(antenna_phase)),
                _ANTENNA_RAD * float(np.sin(antenna_phase + np.pi)),
            ]
        )
        return head, antennas, 0.0


class IdleSway(Tool):
    """进入待机状态:播放呼吸式微动序列。"""

    name = "idle_sway"
    description = (
        "Enter standby mode: the robot gently sways its head (breathing-like), "
        "slowly looks around, and wiggles its antennas subtly. Use when the user "
        "asks Reachy to 待机/休息一下/待命, or when you want to signal the robot "
        "is alive but idle. NOT a dance — movements are deliberately small."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "cycles": {
                "type": "integer",
                "description": "Number of 8-second breathing cycles to play (default 3).",
            },
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        cycles = max(1, int(kwargs.get("cycles", 3)))
        logger.info("Tool call: idle_sway cycles=%d", cycles)
        for _ in range(cycles):
            await deps.reachy_mini.async_play_move(IdleSwayMove(1))
            await asyncio.sleep(0.05)  # 同 dance.py:给 play_move 一点调度间隙
        return {"status": "played", "cycles": cycles}
