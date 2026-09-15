"""P1 head poller 隔离测试(mock ReachyMini,不连 daemon)。"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def mock_reachy():
    """返回一个 fake ReachyMini,有 head pose APIs(返回 numpy 模拟 SDK)。"""
    import numpy as np

    fake = MagicMock()
    fake.get_current_head_pose.return_value = np.eye(4)  # 真实 SDK 返回 numpy
    fake.get_current_joint_positions.return_value = (
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
        [0.01, -0.01],
    )
    return fake


def test_head_poller_writes_state_bus(mock_reachy):
    """Head poller 后台线程应写入 state bus。"""
    from reachymini_conversation.app import ConversationApp
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus

    reset_state_bus()
    bus = get_state_bus()
    bus.update("status", "running")

    app = ConversationApp(head_poll_hz=10.0)  # 10 Hz,快速测试

    stop = threading.Event()
    poller = threading.Thread(
        target=app._head_poller_loop,
        args=(mock_reachy, stop),
        daemon=True,
        name="test-head-poller",
    )
    poller.start()
    time.sleep(0.3)  # 让 poller 跑几次
    stop.set()
    poller.join(timeout=2.0)

    assert not poller.is_alive(), "poller 应在 stop_event set 后退出"

    snap = bus.snapshot()
    assert snap["head_joints"] == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
    assert snap["antennas"] == [0.01, -0.01]
    # head_pose 是 4x4 的 list-of-list
    assert isinstance(snap["head_pose"], list)
    assert len(snap["head_pose"]) == 4
    assert snap["error"] is None


def test_head_poller_handles_exception():
    """ReachyMini 抛异常时,poller 应记错误但不死。"""
    from reachymini_conversation.app import ConversationApp
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus

    reset_state_bus()
    bus = get_state_bus()

    fake = MagicMock()
    fake.get_current_head_pose.side_effect = RuntimeError("mock error")

    app = ConversationApp(head_poll_hz=10.0)
    stop = threading.Event()
    poller = threading.Thread(
        target=app._head_poller_loop,
        args=(fake, stop),
        daemon=True,
    )
    poller.start()
    time.sleep(0.3)
    stop.set()
    poller.join(timeout=2.0)

    snap = bus.snapshot()
    # 应记错误
    assert snap["error"] is not None
    assert "mock error" in snap["error"]
    assert "RuntimeError" in snap["error"]


def test_head_poller_respects_interval(mock_reachy):
    """Poller 间隔 ≈ 1/Hz(允许 ±50% 容差)。"""
    import numpy as np

    from reachymini_conversation.app import ConversationApp
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus

    reset_state_bus()
    get_state_bus()

    # 5 Hz → 200ms 间隔
    app = ConversationApp(head_poll_hz=5.0)
    stop = threading.Event()

    # 数 5 次调用的耗时
    call_times = []

    def fake_pose():
        call_times.append(time.monotonic())
        return np.eye(4)

    def fake_joints():
        return ([0] * 7, [0, 0])

    mock_reachy.get_current_head_pose.side_effect = fake_pose
    mock_reachy.get_current_joint_positions.side_effect = fake_joints

    poller = threading.Thread(
        target=app._head_poller_loop,
        args=(mock_reachy, stop),
        daemon=True,
    )
    poller.start()
    time.sleep(1.2)  # 应调用 ~6 次
    stop.set()
    poller.join(timeout=2.0)

    # 至少 4 次调用
    assert len(call_times) >= 4, f"只调了 {len(call_times)} 次"

    # 间隔检查:相邻间隔应在 100-300ms 之间(目标 200ms,允许抖动)
    intervals = [call_times[i + 1] - call_times[i] for i in range(len(call_times) - 1)]
    for interval in intervals:
        assert 0.05 < interval < 0.5, f"间隔 {interval}s 超出范围"
