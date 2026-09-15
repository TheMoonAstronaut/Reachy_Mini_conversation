"""Fix A(声源跟随开关)+ Fix B(工具动作镜像适配器)的测试。

Fix A 保护的事:
  - SoundLocalizer 默认 enabled=False:有 DoA 数据+语音也**不**驱动转头(只更新徽章)
  - set_enabled(True) 后:语音触发 set_target(body_yaw)
  - set_enabled(False) 后:恢复不驱动
Fix B 保护的事:
  - MirroredToolTarget 的 async_play_move/play_move/goto_target/set_target/look_at_image
    全部镜像到 sim + real 双实例;real=None 时只打 sim;单边异常不扩散。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from reachymini_conversation.mirror_orchestrator import (  # noqa: E402
    MirroredToolTarget,
    MirrorOrchestrator,
)
from reachymini_conversation.state_bus import get_state_bus, reset_state_bus  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_bus():
    reset_state_bus()
    yield
    reset_state_bus()


# ---------- Fix A:SoundLocalizer 开关 ----------


class _FakeDoA:
    def __init__(self, angle_rad: float = 1.57, speech: bool = True) -> None:
        self.angle_rad = angle_rad
        self.speech = speech
        self.available = True

    def get_DoA(self):
        return (self.angle_rad, self.speech)

    def close(self):
        pass


class _FakeOrch:
    def __init__(self) -> None:
        self.yaws: list[float] = []

    async def set_target(self, body_yaw: float = 0.0, **kw: Any) -> None:
        self.yaws.append(body_yaw)


def _make_localizer(orch):
    from reachymini_conversation.sound_localizer import SoundLocalizer

    sl = SoundLocalizer(orchestrator=orch, doa_hz=10.0, return_to_center_sec=99.0)
    sl._doa = _FakeDoA()
    sl._available = True
    return sl


def test_doa_disabled_by_default_drives_nothing():
    orch = _FakeOrch()
    sl = _make_localizer(orch)
    assert sl.enabled is False, "V2:声源跟随必须默认关"
    sl._tick()
    assert orch.yaws == [], "关闭时不得驱动转头"
    # 但徽章数据照常更新
    assert get_state_bus().get("doa_angle") is not None


def test_doa_enabled_drives_yaw():
    orch = _FakeOrch()
    sl = _make_localizer(orch)
    sl.set_enabled(True)
    sl._tick()
    assert len(orch.yaws) == 1, "开启后语音应触发一次转向"
    sl.set_enabled(False)
    sl._tick()
    assert len(orch.yaws) == 1, "再关闭后不再驱动"


# ---------- Fix B:MirroredToolTarget ----------


class _RecMini:
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.calls: list[tuple[str, Any]] = []

    async def async_play_move(self, move):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append(("async_play_move", move))

    def play_move(self, move):
        self.calls.append(("play_move", move))

    def goto_target(self, **kw):
        self.calls.append(("goto_target", kw))

    def set_target(self, **kw):
        self.calls.append(("set_target", kw))

    def look_at_image(self, u, v, duration=0.3):
        self.calls.append(("look_at_image", (u, v, duration)))
        return "ok"


def test_mirrored_target_hits_both():
    sim, real = _RecMini("sim"), _RecMini("real")
    orch = MirrorOrchestrator(sim_mini=sim, real_mini=real, run_mode="real_plus_sim")
    target = MirroredToolTarget(orch)

    asyncio.run(target.async_play_move("move1"))
    target.play_move("move2")
    target.goto_target(head="H", duration=0.5)
    target.set_target(body_yaw=10)
    target.look_at_image(100, 200, duration=0.3)

    for m in (sim, real):
        kinds = [c[0] for c in m.calls]
        assert kinds == [
            "async_play_move", "play_move", "goto_target", "set_target", "look_at_image",
        ], f"{m.name} 漏镜像: {kinds}"


def test_mirrored_target_sim_only_when_no_real():
    sim = _RecMini("sim")
    orch = MirrorOrchestrator(sim_mini=sim, real_mini=None, run_mode="pure_sim")
    target = MirroredToolTarget(orch)
    asyncio.run(target.async_play_move("m"))
    assert len(sim.calls) == 1  # 不崩即可


def test_mirrored_target_partial_failure_tolerated():
    sim, real = _RecMini("sim"), _RecMini("real", fail=True)
    orch = MirrorOrchestrator(sim_mini=sim, real_mini=real, run_mode="real_plus_sim")
    target = MirroredToolTarget(orch)
    asyncio.run(target.async_play_move("m"))  # real 抛错,gather 吞掉
    assert len(sim.calls) == 1, "real 失败不得影响 sim"
