"""P5 sound_localizer + look_at_sound 单元测试。"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ============================================================================
# SoundLocalizer 测试
# ============================================================================
def test_sound_localizer_init():
    """SoundLocalizer 初始化不应该崩(无硬件时优雅降级)。"""
    from reachymini_conversation.sound_localizer import SoundLocalizer

    orch = MagicMock()
    sl = SoundLocalizer(orchestrator=orch, doa_hz=10.0)
    assert sl.doa_hz == 10.0
    assert sl.return_to_center_sec >= 0.5
    assert sl.available is False  # 还没 start,_available 默认 False
    # start 后会尝试加载 AudioDoA → 沙箱无 USB → False


def test_sound_localizer_doa_angle_to_body_yaw_mapping():
    """DoA rad → body_yaw deg 映射公式正确(plan.md §4.2)。

    DoA 0 rad (左) → body_yaw -90
    DoA π/2 rad (前) → body_yaw 0
    DoA π rad (右) → body_yaw +90
    """
    import math

    # 直接测试公式
    for angle_rad, expected_deg in [
        (0.0, -90.0),
        (math.pi / 2, 0.0),
        (math.pi, 90.0),
        (-math.pi / 2, -180.0),  # 后方,转 -180°
    ]:
        actual = math.degrees(angle_rad) - 90.0
        assert abs(actual - expected_deg) < 0.01, f"{angle_rad} → {actual} != {expected_deg}"


def test_sound_localizer_handles_unavailable_hardware():
    """无 ReSpeaker 硬件时,start() 不崩,available=False。"""
    from reachymini_conversation.sound_localizer import SoundLocalizer
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus

    reset_state_bus()

    # Mock AudioDoA 使其 hardware 不可用
    import reachy_mini.media.audio_doa as doa_module

    class FakeAudioDoA:
        @property
        def available(self):
            return False

        def get_DoA(self):
            return None

        def close(self):
            pass

    orig_AudioDoA = doa_module.AudioDoA
    doa_module.AudioDoA = FakeAudioDoA  # type: ignore

    try:
        sl = SoundLocalizer(orchestrator=MagicMock(), doa_hz=20.0)
        sl.start()

        # 等后台线程跑几次
        time.sleep(0.2)

        # 状态应该是 available=False
        bus = get_state_bus()
        assert bus.get("doa_available") is False
        assert sl.available is False

        # 干净关闭
        sl.stop(timeout=2.0)
        assert not (sl._thread and sl._thread.is_alive())
    finally:
        doa_module.AudioDoA = orig_AudioDoA  # type: ignore


def test_sound_localizer_updates_state_on_speech():
    """speech=True 时,state_bus.doa_angle 被更新。"""
    from reachymini_conversation.sound_localizer import SoundLocalizer
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus

    reset_state_bus()

    class FakeAudioDoA:
        @property
        def available(self):
            return True

        def get_DoA(self):
            # π/2 = 前 → body_yaw = 0
            import math

            return (math.pi / 2, True)

        def close(self):
            pass

    import reachy_mini.media.audio_doa as doa_module

    orig = doa_module.AudioDoA
    doa_module.AudioDoA = FakeAudioDoA  # type: ignore

    try:
        sl = SoundLocalizer(orchestrator=MagicMock(), doa_hz=20.0)
        sl.start()
        time.sleep(0.2)

        bus = get_state_bus()
        # doa_angle = degrees(π/2) - 90 = 0
        assert abs(bus.get("doa_angle") - 0.0) < 0.01, f"doa_angle={bus.get('doa_angle')}"
        assert bus.get("doa_speech") is True
        assert bus.get("doa_available") is True

        sl.stop(timeout=2.0)
    finally:
        doa_module.AudioDoA = orig  # type: ignore


def test_sound_localizer_returns_to_center_after_silence():
    """静默超时后,set_target(0.0) 应被调用。"""
    from reachymini_conversation.sound_localizer import SoundLocalizer

    call_args = []

    async def fake_set_target(body_yaw=None, **kwargs):
        call_args.append(body_yaw)

    class FakeOrchestrator:
        def set_target(self, body_yaw=None, **kwargs):
            return fake_set_target(body_yaw=body_yaw, **kwargs)

    class FakeAudioDoA:
        @property
        def available(self):
            return True

        def __init__(self):
            self.call_count = 0

        def get_DoA(self):
            self.call_count += 1
            import math

            # 前 5 次有 speech,之后静默
            if self.call_count <= 5:
                return (math.pi / 4, True)  # 45°+45°= 45° body_yaw
            return (math.pi / 4, False)  # no speech

        def close(self):
            pass

    import reachy_mini.media.audio_doa as doa_module

    orig = doa_module.AudioDoA
    doa_module.AudioDoA = FakeAudioDoA  # type: ignore

    try:
        orch = FakeOrchestrator()
        sl = SoundLocalizer(
            orchestrator=orch,
            doa_hz=50.0,
            return_to_center_sec=0.2,  # 200ms 后回中
        )
        sl.start()
        sl.set_enabled(True)  # V2 Fix A:跟随默认关,本测试显式开启

        # 等足够长时间触发回中(200ms 超时 + 几次 tick)
        time.sleep(0.6)

        sl.stop(timeout=2.0)
    finally:
        doa_module.AudioDoA = orig  # type: ignore

    # 应该看到 0.0(回中)被调用过
    assert 0.0 in call_args, f"回中(0.0)未被调用:calls={call_args}"


def test_sound_localizer_thread_safety():
    """start() 重复调用安全(no-op,不会重复起线程)。"""
    from reachymini_conversation.sound_localizer import SoundLocalizer

    class FakeAudioDoA:
        @property
        def available(self):
            return False

        def get_DoA(self):
            return None

        def close(self):
            pass

    import reachy_mini.media.audio_doa as doa_module

    orig = doa_module.AudioDoA
    doa_module.AudioDoA = FakeAudioDoA  # type: ignore

    try:
        sl = SoundLocalizer(orchestrator=MagicMock())
        sl.start()
        first_thread = sl._thread
        sl.start()  # 第二次调用应该 no-op
        assert sl._thread is first_thread  # 同一线程

        sl.stop(timeout=2.0)
    finally:
        doa_module.AudioDoA = orig  # type: ignore


# ============================================================================
# look_at_sound tool 测试
# ============================================================================
def test_look_at_sound_no_hardware_returns_unavailable():
    """无硬件 → 返回 unavailable。"""
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus
    from tools.look_at_sound import LookAtSoundTool

    reset_state_bus()
    bus = get_state_bus()
    bus.update("doa_available", False)

    deps = MagicMock()
    deps.reachy_mini = MagicMock()

    tool = LookAtSoundTool()
    result = asyncio.run(tool(deps))
    assert result["status"] == "unavailable"
    deps.reachy_mini.set_target.assert_not_called()


def test_look_at_sound_no_data_yet():
    """有硬件但没读到 → 返回 no_data。"""
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus
    from tools.look_at_sound import LookAtSoundTool

    reset_state_bus()
    bus = get_state_bus()
    bus.update("doa_available", True)
    bus.update("doa_angle", None)  # 还没读到

    deps = MagicMock()
    tool = LookAtSoundTool()
    result = asyncio.run(tool(deps))
    assert result["status"] == "no_data"


def test_look_at_sound_calls_set_target_with_angle():
    """有角度 → 调 set_target(body_yaw=...)。"""
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus
    from tools.look_at_sound import LookAtSoundTool

    reset_state_bus()
    bus = get_state_bus()
    bus.update("doa_available", True)
    bus.update("doa_angle", 45.0)
    bus.update("doa_speech", True)

    captured = []

    async def fake_set_target(*args, **kwargs):
        captured.append((args, kwargs))

    deps = MagicMock()
    deps.reachy_mini.set_target.side_effect = fake_set_target

    tool = LookAtSoundTool()
    result = asyncio.run(tool(deps))

    assert result["status"] == "looking"
    assert result["target_yaw_deg"] == 45.0
    assert result["speech_detected"] is True
    assert len(captured) == 1
    args, kwargs = captured[0]
    # kwargs={"body_yaw": 45.0} 或 args 里包含
    assert kwargs.get("body_yaw") == 45.0 or (len(args) >= 1 and args[0] == 45.0)


def test_look_at_sound_handles_set_target_exception():
    """set_target 失败 → 返回 error,不抛。"""
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus
    from tools.look_at_sound import LookAtSoundTool

    reset_state_bus()
    bus = get_state_bus()
    bus.update("doa_available", True)
    bus.update("doa_angle", 0.0)

    deps = MagicMock()

    async def fake_set_target(*args, **kwargs):
        raise RuntimeError("robot offline")

    deps.reachy_mini.set_target = fake_set_target

    tool = LookAtSoundTool()
    result = asyncio.run(tool(deps))
    assert "error" in result
    assert "robot offline" in result["error"]


# ============================================================================
# tools 自动注册测试(P0.4 已测过,这里再次确认 look_at_sound 注册成功)
# ============================================================================
def test_look_at_sound_registered_in_all_tools():
    """look_at_sound 应自动注册到 ALL_TOOLS(因为它被 tools/__init__.py 扫描到)。"""
    import tools

    assert (
        "look_at_sound" in tools.ALL_TOOLS
    ), "look_at_sound 应该通过 tools/core_tools._initialize_tools() 自动注册"
