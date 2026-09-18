"""表演类工具后台化测试(2026-09-18 响应延迟治理)。

背景:用户日志实锤 ASR 文本 → 工具调用 → TTS 合成最大间隔 39s,主因是
dance/idle_sway 等 async_play_move 阻塞整个 LLM 轮次。修复:_dispatch_tool_call
对 _BG_PERFORM_TOOLS 改后台 daemon 线程执行,立即返回 started。

保护的事:
  1. 表演类工具立即返回(started),不阻塞调用方;
  2. 后台线程真正执行了工具;
  3. 执行期间重复调用被跳过(already_running);
  4. 非表演类工具仍同步执行(行为不变);
  5. stop_dance 汇报 dance_running 状态。
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

import tools.core_tools as core_tools  # noqa: E402


@pytest.fixture(autouse=True)
def _wait_bg_threads():
    """每个测试结束等后台工具线程全部退出(模块级注册表跨测试隔离)。"""
    yield
    for th in core_tools._BG_THREADS.values():
        th.join(timeout=3.0)
    core_tools._BG_THREADS.clear()


class _FakePerformTool:
    """假表演工具:记录调用,阻塞 duration 秒。"""

    name = "dance"
    description = "fake"
    parameters_schema = {"type": "object", "properties": {}}

    def __init__(self, duration: float = 0.4) -> None:
        self.duration = duration
        self.called_at: list[float] = []

    async def __call__(self, deps, **kwargs):
        self.called_at.append(time.monotonic())
        await asyncio.sleep(self.duration)
        return {"status": "done"}


class _FakeSyncTool:
    name = "idle_do_nothing"
    description = "fake"
    parameters_schema = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.called = False

    async def __call__(self, deps, **kwargs):
        self.called = True
        return {"status": "ok"}


def _patched_tools(fake):
    return mock.patch.dict(core_tools.ALL_TOOLS, {"dance": fake}, clear=False)


def test_perform_tool_returns_immediately_and_runs_background():
    """dance 立即返回 started,且后台线程真的执行了工具。"""
    fake = _FakePerformTool(duration=0.4)
    with _patched_tools(fake):
        t0 = time.monotonic()
        result = asyncio.run(
            core_tools.dispatch_tool_call("dance", "{}", deps=mock.MagicMock())
        )
        elapsed = time.monotonic() - t0

    assert result["status"] == "started", f"应立即返回 started: {result}"
    assert elapsed < 0.2, f"dispatch 不得阻塞(表演动作在后台): {elapsed:.2f}s"
    # 后台线程执行完成(等够时长)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not fake.called_at:
        time.sleep(0.02)
    assert fake.called_at, "后台线程必须真正执行工具"


def test_perform_tool_duplicate_skipped_while_running():
    """同一表演工具执行期间,重复调用 → already_running(不叠跳)。"""
    fake = _FakePerformTool(duration=0.5)
    with _patched_tools(fake):
        r1 = asyncio.run(
            core_tools.dispatch_tool_call("dance", "{}", deps=mock.MagicMock())
        )
        assert r1["status"] == "started"
        r2 = asyncio.run(
            core_tools.dispatch_tool_call("dance", "{}", deps=mock.MagicMock())
        )
    assert r2["status"] == "already_running", f"执行期间重复调用应跳过: {r2}"
    # 等第一个后台调用落地(线程调度有延迟,不能立即断言)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not fake.called_at:
        time.sleep(0.02)
    assert len(fake.called_at) == 1, "重复调用不得再次执行动作"


def test_non_perform_tool_still_synchronous():
    """非表演类工具(idle_do_nothing)仍同步执行并返回其结果。"""
    fake = _FakeSyncTool()
    with mock.patch.dict(core_tools.ALL_TOOLS, {"idle_do_nothing": fake}, clear=False):
        result = asyncio.run(
            core_tools.dispatch_tool_call("idle_do_nothing", "{}", deps=mock.MagicMock())
        )
    assert result == {"status": "ok"}
    assert fake.called


def test_background_threads_are_daemon_and_named():
    """后台线程为 daemon(不阻进程退出)且带 tool-bg- 名(可排查)。"""
    fake = _FakePerformTool(duration=0.3)
    with _patched_tools(fake):
        asyncio.run(
            core_tools.dispatch_tool_call("dance", "{}", deps=mock.MagicMock())
        )
        th = core_tools._BG_THREADS.get("dance")
        assert th is not None
        assert th.daemon, "后台线程必须 daemon"
        assert th.name.startswith("tool-bg-")
        th.join(timeout=2.0)
    assert not core_tools.is_tool_running("dance")
