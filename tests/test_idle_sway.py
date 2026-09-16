"""待机微动工具测试(tools.idle_sway / IdleSwayMove)。

保护的事:
  1. Move 接口契约:duration = 8s × cycles;evaluate 返回 (4x4 head, (2,) antennas, body_yaw)。
  2. 幅值安全:待机是微动 —— pitch/yaw/roll/z/天线都在小界限内。
  3. 工具行为:cycles 参数控制播放次数;缺省 3;调 async_play_move。
  4. 注册:core_tools 工具表含 idle_sway;"待机/休息/待命"关键词映射到它。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from tools.idle_sway import (  # noqa: E402
    _ANTENNA_RAD,
    _PITCH_RAD,
    _YAW_RAD,
    _Z_M,
    IdleSway,
    IdleSwayMove,
)


class TestIdleSwayMove:
    def test_duration_scales_with_cycles(self) -> None:
        assert IdleSwayMove(1).duration == 8.0
        assert IdleSwayMove(3).duration == 24.0

    def test_evaluate_shapes(self) -> None:
        move = IdleSwayMove()
        for t in (0.0, 2.0, 4.0, 7.9):
            head, antennas, body_yaw = move.evaluate(t)
            assert head.shape == (4, 4)
            assert antennas.shape == (2,)
            assert body_yaw == 0.0

    def test_amplitudes_stay_small(self) -> None:
        """待机微动:所有自由度幅值不得超过设计界限(防变成"舞蹈")。"""
        move = IdleSwayMove()
        max_pitch = max_yaw = max_z = max_ant = 0.0
        for t in np.linspace(0, 16.0, 400):
            head, antennas, _ = move.evaluate(float(t))
            # head 4x4:pitch/yaw 从旋转矩阵近似读出小角
            pitch = float(np.arcsin(np.clip(head[2, 0], -1, 1)))
            yaw = float(np.arctan2(head[1, 0], head[0, 0]))
            z = float(head[2, 3])
            max_pitch = max(max_pitch, abs(pitch))
            max_yaw = max(max_yaw, abs(yaw))
            max_z = max(max_z, abs(z))
            max_ant = max(max_ant, float(np.abs(antennas).max()))
        assert max_pitch <= _PITCH_RAD + 1e-3
        assert max_yaw <= _YAW_RAD + 1e-3
        assert max_z <= _Z_M + 1e-6
        assert max_ant <= _ANTENNA_RAD + 1e-6

    def test_antennas_counter_phase(self) -> None:
        """天线错相:任意时刻两天线方向相反(可爱感,非同相机械摆)。"""
        move = IdleSwayMove()
        _, antennas, _ = move.evaluate(1.3)
        assert antennas[0] * antennas[1] <= 0


class _FakeDeps:
    def __init__(self) -> None:
        self.plays: list = []

        class _R:
            def __init__(self, outer):
                self._o = outer

            async def async_play_move(self, move):
                self._o.plays.append(move)

        self.reachy_mini = _R(self)


class TestIdleSwayTool:
    def test_plays_cycles_times(self) -> None:
        deps = _FakeDeps()
        r = asyncio.run(IdleSway()(deps, cycles=3))
        assert r["status"] == "played" and r["cycles"] == 3
        assert len(deps.plays) == 3
        assert all(isinstance(m, IdleSwayMove) for m in deps.plays)

    def test_default_cycles(self) -> None:
        deps = _FakeDeps()
        asyncio.run(IdleSway()(deps))
        assert len(deps.plays) == 3


class TestRegistration:
    def test_tool_registered(self) -> None:
        from tools.core_tools import ALL_TOOLS

        assert "idle_sway" in ALL_TOOLS

    def test_brain_keyword_mapping(self) -> None:
        from reachymini_conversation.brain.doubao_brain import _TOOL_NAME_MAP

        assert _TOOL_NAME_MAP["待机"] == "idle_sway"
        assert _TOOL_NAME_MAP["休息"] == "idle_sway"
        assert _TOOL_NAME_MAP["不动"] == "idle_do_nothing"  # 完全静止仍有入口
