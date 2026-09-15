"""ModeManager + orchestrator 运行时插拔的测试(V2.1)。

保护的事:
  1. orchestrator.attach_real/detach_real:run_mode 与 real_mini 同步变更,
     镜像调用在 attach 后同时打 sim+real,detach 后只打 sim。
  2. ModeManager.switch_to 状态机:
     - pure_sim → real_plus_sim 成功路径(wired:daemon 起→就绪→client→attach)
     - 失败路径(daemon 不就绪 / client 抛错 / 健康检查挂)→ 回滚 pure_sim + error,
       且 daemon/client 半成品都被清理
     - real_plus_sim → pure_sim:detach + client 关闭 + daemon 停止
     - 重复切到当前模式:no-op
     - 未知模式:报错不动作
  3. 全部用 fake,不起真进程、不连真机。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from reachymini_conversation.mirror_orchestrator import MirrorOrchestrator  # noqa: E402
from reachymini_conversation.mode_manager import ModeManager  # noqa: E402
from reachymini_conversation.state_bus import get_state_bus, reset_state_bus  # noqa: E402


class _FakeMini:
    """记录调用的假 ReachyMini。"""

    def __init__(self, name: str, healthy: bool = True) -> None:
        self.name = name
        self.healthy = healthy
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    async def goto_target(self, **kwargs):
        self.calls.append(("goto_target", kwargs))

    def get_current_joint_positions(self):
        if not self.healthy:
            raise RuntimeError("link dead")
        return ([0.0] * 7, [0.0] * 2)

    def close(self):
        self.closed = True


class _FakeDaemonRunner:
    """假 daemon B:记录 start/stop,就绪与否可脚本化。"""

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def wait_ready(self, timeout_s: float = 90.0) -> bool:
        return self.ready

    def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def _clean_bus():
    reset_state_bus()
    yield
    reset_state_bus()


def _make(cfg_type: str = "wired", *, mini_ok: bool = True, daemon_ok: bool = True):
    sim = _FakeMini("sim")
    orch = MirrorOrchestrator(sim_mini=sim, real_mini=None, run_mode="pure_sim")
    real = _FakeMini("real", healthy=mini_ok)
    daemon = _FakeDaemonRunner(ready=daemon_ok)

    def factory(cfg):
        return real

    mm = ModeManager(orch, client_factory=factory, daemon_runner=daemon)
    return orch, sim, real, daemon, mm


# ---------- orchestrator 插拔 ----------


def test_attach_detach_mirroring():
    import asyncio

    async def _run():
        orch, sim, real, _, _ = _make()
        await orch.goto_target(head=None, antennas=None, duration=0.1, body_yaw=0.0)
        assert len(sim.calls) == 1 and len(real.calls) == 0

        orch.attach_real(real)
        assert orch.run_mode == "real_plus_sim"
        await orch.goto_target(head=None, antennas=None, duration=0.1, body_yaw=0.0)
        assert len(sim.calls) == 2 and len(real.calls) == 1, "attach 后应镜像到两边"

        old = orch.detach_real()
        assert old is real and orch.run_mode == "pure_sim"
        await orch.goto_target(head=None, antennas=None, duration=0.1, body_yaw=0.0)
        assert len(sim.calls) == 3 and len(real.calls) == 1, "detach 后只打 sim"

    asyncio.run(_run())


# ---------- ModeManager 状态机 ----------


def test_switch_to_real_success_wired():
    orch, sim, real, daemon, mm = _make()
    r = mm.switch_to("real_plus_sim", {"type": "wired", "port": 8001})
    assert r["ok"] and r["run_mode"] == "real_plus_sim"
    assert daemon.started, "有线模式必须先起 daemon B"
    assert orch.real_mini is real
    assert get_state_bus().get("run_mode") == "real_plus_sim"


def test_switch_to_real_daemon_not_ready_rolls_back():
    orch, sim, real, daemon, mm = _make(daemon_ok=False)
    r = mm.switch_to("real_plus_sim", {"type": "wired"})
    assert not r["ok"] and r["run_mode"] == "pure_sim"
    assert "真机连接失败" in (r["error"] or "")
    assert orch.real_mini is None
    assert daemon.stopped, "失败路径必须停掉 daemon B 半成品"
    assert get_state_bus().get("run_mode") == "pure_sim"
    assert get_state_bus().get("error")


def test_switch_to_real_client_dead_rolls_back():
    orch, sim, real, daemon, mm = _make(mini_ok=False)  # 健康检查会挂
    r = mm.switch_to("real_plus_sim", {"type": "wired"})
    assert not r["ok"] and r["run_mode"] == "pure_sim"
    assert real.closed, "失败路径必须关 client"
    assert daemon.stopped
    assert orch.real_mini is None


def test_switch_back_to_pure_sim():
    orch, sim, real, daemon, mm = _make()
    assert mm.switch_to("real_plus_sim", {"type": "wired"})["ok"]
    r = mm.switch_to("pure_sim", {"type": "wired"})
    assert r["ok"] and r["run_mode"] == "pure_sim"
    assert real.closed
    assert daemon.stopped
    assert orch.real_mini is None


def test_switch_noop_and_unknown():
    orch, *_ , mm = _make()
    r = mm.switch_to("pure_sim", {"type": "wired"})  # 已是 pure_sim
    assert r["ok"] and r["run_mode"] == "pure_sim"
    r2 = mm.switch_to("mars_mode", {"type": "wired"})
    assert not r2["ok"] and "未知模式" in (r2["error"] or "")


def test_wireless_skips_daemon():
    orch, sim, real, daemon, mm = _make()
    r = mm.switch_to("real_plus_sim", {"type": "wireless", "host": "reachy-mini.local"})
    assert r["ok"]
    assert not daemon.started, "无线模式不起本机 daemon B"
    assert orch.real_mini is real
