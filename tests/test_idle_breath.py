"""空闲待机呼吸测试(idle_breath.BreathingMove + IdleBreathController)。

移植源:GitHub pollen-robotics/reachy_mini_conversation_app
  moves.py BreathingMove(z ±5mm @0.1Hz,天线 ±15° @0.5Hz 反向,
  neutral antennas ±10°,1s 插值进入)。

保护的事:
  1. Move 契约:duration = 1s 插值 + 8s×cycles;evaluate 返回 (4x4, (2,), 0.0)。
  2. 官方参数:z 幅值 ≤5mm;天线幅值 ≤15° 且反向;neutral 起点插值。
  3. t=0 时 head == 插值起点(平滑进入,无跳变)。
  4. Controller:空闲超时播放;活动中不播放;播放段间发现活动即停;stop 干净退出。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from reachy_mini.utils import create_head_pose  # noqa: E402

from reachymini_conversation.idle_breath import (  # noqa: E402
    ANTENNA_SWAY_RAD,
    BREATH_Z_AMPLITUDE_M,
    NEUTRAL_ANTENNAS,
    BreathingMove,
    IdleBreathController,
)
from reachymini_conversation.state_bus import get_state_bus, reset_state_bus  # noqa: E402

_START_POSE = create_head_pose(x=0.01, y=0, z=0.01, roll=0, pitch=0, yaw=0, degrees=True, mm=False)
_START_ANT = (0.3, -0.3)


class TestBreathingMove:
    def test_duration(self) -> None:
        assert BreathingMove(_START_POSE, _START_ANT, cycles=1).duration == pytest.approx(9.0)
        assert BreathingMove(_START_POSE, _START_ANT, cycles=3).duration == pytest.approx(25.0)

    def test_starts_at_interpolation_pose(self) -> None:
        """t=0 必须等于插值起点(防跳变)。"""
        move = BreathingMove(_START_POSE, _START_ANT)
        head, antennas, body_yaw = move.evaluate(0.0)
        np.testing.assert_allclose(head, _START_POSE, atol=1e-6)
        np.testing.assert_allclose(antennas, _START_ANT, atol=1e-6)
        assert body_yaw == 0.0

    def test_interp_end_approaches_neutral(self) -> None:
        """插值段末(t→1⁻)应接近 neutral(head 与天线)。"""
        move = BreathingMove(_START_POSE, _START_ANT)
        head, antennas, _ = move.evaluate(0.999)
        neutral = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        np.testing.assert_allclose(head, neutral, atol=1e-3)
        np.testing.assert_allclose(antennas, NEUTRAL_ANTENNAS, atol=1e-3)

    def test_breath_phase_antennas_swing_around_zero(self) -> None:
        """呼吸段天线从 0 起摆(官方行为:不叠加 neutral ±10° 偏移)。"""
        move = BreathingMove(_START_POSE, _START_ANT)
        _, antennas, _ = move.evaluate(1.0 + 1e-9)
        np.testing.assert_allclose(antennas, [0.0, 0.0], atol=1e-6)

    def test_breath_amplitudes_official(self) -> None:
        """呼吸段:z ≤ 5mm、天线 ≤ 15° 反向(官方参数)。"""
        move = BreathingMove(_START_POSE, _START_ANT, cycles=2)
        max_z = max_ant = 0.0
        for t in np.linspace(1.01, move.duration - 0.01, 300):
            head, antennas, _ = move.evaluate(float(t))
            max_z = max(max_z, abs(float(head[2, 3])))
            max_ant = max(max_ant, float(np.abs(antennas).max()))
            assert antennas[0] == pytest.approx(-antennas[1])  # 官方:反向摆动
        assert max_z <= BREATH_Z_AMPLITUDE_M + 1e-6
        assert max_ant <= ANTENNA_SWAY_RAD + 1e-6
        assert max_z > 0.004  # 确实在动(不是死寂)


class _FakeTarget:
    def __init__(self) -> None:
        self.played: list = []

    async def async_play_move(self, move):
        self.played.append(move)
        # 模拟播放耗时(远快于真实 9s,测试不等)
        await __import__("asyncio").sleep(0.01)


class TestIdleBreathController:
    def setup_method(self) -> None:
        reset_state_bus()

    def teardown_method(self) -> None:
        reset_state_bus()

    def test_plays_after_idle_timeout(self) -> None:
        target = _FakeTarget()
        ctl = IdleBreathController(target, idle_after_s=0.3, check_interval_s=0.05)
        ctl.start()
        try:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not target.played:
                time.sleep(0.05)
            assert target.played, "空闲超时后应播放呼吸"
            assert isinstance(target.played[0], BreathingMove)
        finally:
            ctl.stop()

    def test_no_play_when_active(self) -> None:
        target = _FakeTarget()
        ctl = IdleBreathController(target, idle_after_s=0.3, check_interval_s=0.05)
        ctl.start()
        try:
            # 持续制造活动(last_reply 变化)
            deadline = time.monotonic() + 1.0
            i = 0
            while time.monotonic() < deadline:
                i += 1
                get_state_bus().update("last_reply", f"活动{i}")
                time.sleep(0.1)
            assert not target.played, "有活动时不应播放"
        finally:
            ctl.stop()

    def test_no_play_when_status_not_idle(self) -> None:
        target = _FakeTarget()
        get_state_bus().update("status", "speaking")
        ctl = IdleBreathController(target, idle_after_s=0.2, check_interval_s=0.05)
        ctl.start()
        try:
            time.sleep(0.8)
            assert not target.played, "TTS 播放期(status=speaking)不应呼吸"
        finally:
            ctl.stop()

    def test_stop_is_clean(self) -> None:
        ctl = IdleBreathController(_FakeTarget(), idle_after_s=0.2, check_interval_s=0.05)
        ctl.start()
        ctl.stop()
        assert ctl._thread is None
