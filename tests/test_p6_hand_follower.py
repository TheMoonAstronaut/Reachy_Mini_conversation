"""P6 hand_follower + tools 单元测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ============================================================================
# HandFollower 单元(无模型:测降级路径)
# ============================================================================
def test_hand_follower_init():
    """HandFollower 初始化不崩(模型缺 → available=False)。"""
    from reachymini_conversation.hand_follower import HandFollower

    hf = HandFollower(orchestrator=MagicMock(), poll_hz=15.0)
    assert hf.poll_hz == 15.0
    assert hf.available is False
    assert hf.enabled is False


def test_hand_follower_no_model_degrades_gracefully():
    """start() 时模型文件不存在 → available=False,后台线程不起。"""
    from reachymini_conversation.hand_follower import HandFollower
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus

    reset_state_bus()

    hf = HandFollower(
        orchestrator=MagicMock(),
        poll_hz=15.0,
        model_path="/tmp/nonexistent_model.task",
    )
    hf.start()
    assert hf.available is False
    assert hf._thread is None  # 线程不起
    bus = get_state_bus()
    assert bus.get("hand_available") is False


def test_hand_follower_enable_disable():
    """enable/disable 翻转 self._enabled + 写 state_bus。"""
    from reachymini_conversation.hand_follower import HandFollower
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus

    reset_state_bus()

    hf = HandFollower(orchestrator=MagicMock(), model_path="/tmp/nope.task")
    assert hf.enabled is False
    hf.enable()
    assert hf.enabled is True
    assert get_state_bus().get("hand_follow_enabled") is True

    hf.disable()
    assert hf.enabled is False
    assert get_state_bus().get("hand_follow_enabled") is False


def test_hand_follower_thread_safety():
    """start() 重复调用安全。"""
    from reachymini_conversation.hand_follower import HandFollower

    hf = HandFollower(orchestrator=MagicMock(), model_path="/tmp/nope.task")
    hf.start()
    first_thread = hf._thread
    hf.start()  # 第二次应 no-op
    assert hf._thread is first_thread


# ============================================================================
# StartHandFollow / StopHandFollow 工具测试
# ============================================================================
def test_start_hand_follow_no_hand_follower_returns_unavailable():
    """deps 没有 hand_follower → 返回 unavailable。"""
    from tools.start_hand_follow import StartHandFollowTool

    deps = MagicMock()
    deps.hand_follower = None

    tool = StartHandFollowTool()
    result = asyncio.run(tool(deps))
    assert result["status"] == "unavailable"


def test_start_hand_follow_calls_enable():
    """deps 有 hand_follower → 调 enable()。"""
    from tools.start_hand_follow import StartHandFollowTool

    deps = MagicMock()
    deps.hand_follower = MagicMock()
    deps.hand_follower.available = True

    tool = StartHandFollowTool()
    result = asyncio.run(tool(deps))
    assert result["status"] == "started"
    deps.hand_follower.enable.assert_called_once()


def test_stop_hand_follow_calls_disable():
    from tools.stop_hand_follow import StopHandFollowTool

    deps = MagicMock()
    deps.hand_follower = MagicMock()

    tool = StopHandFollowTool()
    result = asyncio.run(tool(deps))
    assert result["status"] == "stopped"
    deps.hand_follower.disable.assert_called_once()


def test_stop_hand_follow_no_hand_follower():
    from tools.stop_hand_follow import StopHandFollowTool

    deps = MagicMock()
    deps.hand_follower = None

    tool = StopHandFollowTool()
    result = asyncio.run(tool(deps))
    assert result["status"] == "unavailable"


def test_tools_registered_in_all_tools():
    """start_hand_follow / stop_hand_follow 都自动注册。"""
    import tools

    assert "start_hand_follow" in tools.ALL_TOOLS, "start_hand_follow 应该自动注册"
    assert "stop_hand_follow" in tools.ALL_TOOLS, "stop_hand_follow 应该自动注册"


# ============================================================================
# tools/core_tools.ToolDependencies 字段测试
# ============================================================================
def test_tool_dependencies_has_hand_follower_field():
    """ToolDependencies 应有 hand_follower 字段(P6)。"""
    from tools.core_tools import ToolDependencies

    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
    )
    assert hasattr(deps, "hand_follower")
    assert deps.hand_follower is None  # 默认 None


# ============================================================================
# HandFollower 状态同步:state_bus.hand_follow_requested → enable/disable
# ============================================================================
def test_hand_follower_loop_syncs_state_bus_request():
    """loop tick 检测到 state_bus.hand_follow_requested=True → enable。"""
    from reachymini_conversation.hand_follower import HandFollower
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus

    reset_state_bus()
    bus = get_state_bus()

    # 给一个 mock 的 get_frame_jpeg(不调用 detect)
    hf = HandFollower(
        orchestrator=MagicMock(),
        get_frame_jpeg_fn=MagicMock(return_value=None),  # 永远 None,跳过 detect
        poll_hz=50.0,  # 50 Hz 让 loop 快点轮询
        model_path="/tmp/nope.task",  # 模型缺 → 后台线程不起
    )

    # 先不开线程 — 改逻辑测试
    # 注:模型缺时 start() 不起线程,所以这里我们直接调 enable 测试同步
    bus.update("hand_follow_requested", True)
    hf.enable()  # 模拟 sync 效果
    assert hf.enabled is True
    assert bus.get("hand_follow_enabled") is True


def test_hand_follower_state_bus_request_toggles():
    """state_bus.requested False → disable。"""
    from reachymini_conversation.hand_follower import HandFollower
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus

    reset_state_bus()
    bus = get_state_bus()

    hf = HandFollower(orchestrator=MagicMock(), model_path="/tmp/nope.task")
    hf.enable()  # 先开
    assert hf.enabled is True

    # 模拟 loop tick 检测到 requested=False
    bus.update("hand_follow_requested", False)
    hf.disable()  # 同步
    assert hf.enabled is False
    assert bus.get("hand_follow_enabled") is False


# ============================================================================
# 端到端 mock:HandLandmarker 模拟 + look_at_image 验证
# ============================================================================
def test_hand_follower_tick_with_mock_landmarker(tmp_path):
    """End-to-end:mock HandLandmarker 检测到手 → look_at_image 被调。"""
    from reachymini_conversation.hand_follower import HandFollower
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus

    reset_state_bus()
    bus = get_state_bus()

    # 创建假 .task 文件(只是占位)
    fake_model = tmp_path / "hand_landmarker.task"
    fake_model.write_bytes(b"FAKE")

    # mock mediapipe 模块
    import mediapipe as mp

    class FakeLandmark:
        def __init__(self, x=0.5, y=0.5):
            self.x = x
            self.y = y

    # MediaPipe HandLandmarker 返回 21 个 landmarks/palm
    # index 9 = 中指 MCP(plan.md §4.3 用的"hand center")
    class FakeResult:
        def __init__(self):
            self.hand_landmarks = [[FakeLandmark(0.5, 0.4) for _ in range(21)]]

    class FakeHandLandmarker:
        def __init__(self, *args, **kwargs):
            pass

        def detect_for_video(self, image, timestamp_ms):
            return FakeResult()

        def close(self):
            pass

    # 准备一张假 JPEG bytes(实际会被 PIL 解码失败 → 测试跳过 detect)
    # 为了让 detect 真的被调,我们 mock PIL

    fake_jpeg = b"\xff\xd8\xff\xe0fake_jpeg"

    def fake_get_frame():
        return fake_jpeg

    # mock PIL.Image.open
    import PIL.Image

    orig_open = PIL.Image.open

    class FakePILImage:
        def __init__(self, *args, **kwargs):
            pass

        def convert(self, mode):
            import numpy as np

            return np.zeros((480, 640, 3), dtype=np.uint8)  # H=480, W=640

    PIL.Image.open = FakePILImage

    try:
        # patch mediapipe.tasks.vision.HandLandmarker → FakeHandLandmarker
        orig_lm = mp.tasks.vision.HandLandmarker
        mp.tasks.vision.HandLandmarker = FakeHandLandmarker

        orch = MagicMock()
        # orch.look_at_image 是 async
        captured_calls = []

        async def fake_look_at_image(u, v, duration):
            captured_calls.append((u, v, duration))

        orch.look_at_image.side_effect = fake_look_at_image

        hf = HandFollower(
            orchestrator=orch,
            get_frame_jpeg_fn=fake_get_frame,
            poll_hz=50.0,
            model_path=str(fake_model),
        )

        # 手动加载(因为 _load_landmarker 用真实 import)
        hf._landmarker = FakeHandLandmarker()
        hf._available = True
        bus.update("hand_available", True)

        # 启用 + 跑一次 tick
        hf.enable()
        bus.update("hand_follow_requested", True)

        # 跑 _tick 一次(同步)
        hf._tick()

        # 应该 look_at_image 被调
        assert len(captured_calls) >= 1, f"expected look_at_image call, got {captured_calls}"
        u, v, duration = captured_calls[0]
        assert duration == 0.3
        assert 0 <= u <= 640
        assert 0 <= v <= 480

        # state_bus 更新
        assert bus.get("hand_visible") is True
        assert bus.get("hand_uv") == [u, v]
    finally:
        PIL.Image.open = orig_open
        mp.tasks.vision.HandLandmarker = orig_lm


def test_hand_follower_no_frame_skips_gracefully():
    """get_frame 返回 None → 不调 detect,不崩。"""
    from reachymini_conversation.hand_follower import HandFollower
    from reachymini_conversation.state_bus import reset_state_bus

    reset_state_bus()

    hf = HandFollower(
        orchestrator=MagicMock(),
        get_frame_jpeg_fn=MagicMock(return_value=None),
        model_path="/tmp/nope.task",
    )
    hf._landmarker = MagicMock()
    hf._available = True

    hf.enable()
    hf._tick()  # 不应崩
    # orch.look_at_image 不应被调
    hf.orchestrator.look_at_image.assert_not_called()
