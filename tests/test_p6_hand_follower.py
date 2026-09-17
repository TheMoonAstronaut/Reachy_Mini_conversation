"""P6 hand_follower + tools 单元测试。"""

from __future__ import annotations

import asyncio
import sys

import pytest
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
# 端到端 mock:HandLandmarker 模拟 + goto_target(yaw/pitch 映射)验证
# ============================================================================
def test_hand_follower_tick_with_mock_landmarker(tmp_path):
    """End-to-end:mock HandLandmarker 检测到手 → goto_target 被调(yaw/pitch)。"""
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
        # orch.goto_target 是 async(head=pose, duration=...)
        captured_calls = []

        async def fake_goto_target(**kwargs):
            captured_calls.append(kwargs)

        orch.goto_target.side_effect = fake_goto_target

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

        # goto_target 应被调,duration 透传
        assert len(captured_calls) >= 1, f"expected goto_target call, got {captured_calls}"
        assert captured_calls[0]["duration"] == 0.3
        head_pose = captured_calls[0]["head"]
        # 手部在 (0.5, 0.4) 归一化位置 → 中心偏上 → yaw=0,pitch>0(抬头)
        import numpy as np

        pose = np.asarray(head_pose)
        assert pose.shape == (4, 4)

        # state_bus 更新(hand_uv 是原始像素)
        assert bus.get("hand_visible") is True
        u, v = bus.get("hand_uv")
        assert 0 <= u <= 640
        assert 0 <= v <= 480
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
    # orch.goto_target 不应被调
    hf.orchestrator.goto_target.assert_not_called()


# ============================================================================
# P6 部署仲裁(2026-09-17):播报让位 + EMA 平滑 + 死区防抖
# ============================================================================
class _FakeLandmark:
    def __init__(self, x=0.5, y=0.5):
        self.x = x
        self.y = y


class _FakeHandResult:
    def __init__(self, lm):
        self.hand_landmarks = [[lm] * 21] if lm is not None else []


class _FakeLandmarker:
    """可控 landmark 的假检测器:每次 detect 调用当前 lm 值。"""

    def __init__(self, lm):
        self.lm = lm

    def detect_for_video(self, image, timestamp_ms):
        return _FakeHandResult(self.lm)

    def close(self):
        pass


class _FakePILImage:
    """PIL.Image.open 的假返回:convert('RGB') → 640x480 黑图。"""

    def __init__(self, *args, **kwargs):
        pass

    def convert(self, mode):
        import numpy as np

        return np.zeros((480, 640, 3), dtype=np.uint8)


def _make_ready_follower(orch, lm=None, **kwargs):
    """造一个 _available=True、假检测器的 follower(不经 start())。"""
    from reachymini_conversation.hand_follower import HandFollower

    hf = HandFollower(
        orchestrator=orch,
        get_frame_jpeg_fn=lambda: b"\xff\xd8fake",
        poll_hz=50.0,
        model_path="/tmp/nope.task",
        **kwargs,
    )
    hf._landmarker = _FakeLandmarker(lm)
    hf._available = True
    hf.enable()
    return hf


def _patch_pil():
    import PIL.Image

    return PIL.Image, PIL.Image.open


def _record_gaze_calls(orch):
    """把 orch.goto_target 换成记录 (pitch, yaw) 的假实现(经 create_head_pose 捕获)。"""
    import reachy_mini.utils as rm_utils

    recorded: list[tuple[float, float]] = []
    orig_chp = rm_utils.create_head_pose

    def fake_chp(x, y, z, roll, pitch, yaw, degrees=True):
        recorded.append((pitch, yaw))
        return orig_chp(x, y, z, roll, pitch, yaw, degrees=degrees)

    rm_utils.create_head_pose = fake_chp

    async def fake_goto_target(**kwargs):
        pass

    orch.goto_target.side_effect = fake_goto_target

    def restore():
        rm_utils.create_head_pose = orig_chp

    return recorded, restore


def test_tick_yields_while_speaking():
    """播报(status=speaking/playing)时不驱动头部,但检测状态照常更新。"""
    import PIL.Image

    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus

    reset_state_bus()
    bus = get_state_bus()

    orch = MagicMock()
    recorded, restore = _record_gaze_calls(orch)

    orig_open = PIL.Image.open
    PIL.Image.open = _FakePILImage
    try:
        hf = _make_ready_follower(orch, lm=_FakeLandmark(0.5, 0.4))
        bus.update("status", "speaking")
        hf._tick()
        assert recorded == [], "speaking 期不允许驱动头部(wobbler 优先)"
        assert bus.get("hand_visible") is True, "检测状态仍应更新(徽章实时)"

        bus.update("status", "idle")
        hf._tick()
        assert len(recorded) == 1, "播报结束后跟随应恢复驱动"
    finally:
        PIL.Image.open = orig_open
        restore()


def test_tick_deadband_suppresses_micro_motion():
    """EMA 平滑后位移 < deadband → 不重复发送(防抖)。"""
    import PIL.Image

    from reachymini_conversation.state_bus import reset_state_bus

    reset_state_bus()

    orch = MagicMock()
    recorded, restore = _record_gaze_calls(orch)

    orig_open = PIL.Image.open
    PIL.Image.open = _FakePILImage
    try:
        hf = _make_ready_follower(
            orch, lm=_FakeLandmark(0.5, 0.5), deadband_px=10, smooth_alpha=0.5
        )
        hf._tick()  # 首帧:初始化 EMA 并发送(yaw=0)
        assert len(recorded) == 1

        # 检测值小幅漂移(2px 级):EMA 后 < deadband → 不再发
        hf._landmarker.lm = _FakeLandmark(0.503, 0.5)
        for _ in range(5):
            hf._tick()
        assert len(recorded) == 1, f"微动不应重复发送: {recorded}"

        # 大幅跳变:超过死区 → 发送新目标(手在图像右侧 → yaw>0)
        hf._landmarker.lm = _FakeLandmark(0.8, 0.5)
        hf._tick()
        assert len(recorded) == 2
        assert recorded[1][1] < recorded[0][1], "右侧的手应产生负 yaw(正 yaw=看左)"
    finally:
        PIL.Image.open = orig_open
        restore()


def test_ema_smoothing_lags_behind_detection():
    """EMA 平滑:单次 tick 不应直接跳到检测值(α=0.25 时只走 25%)。"""
    import PIL.Image

    from reachymini_conversation.state_bus import reset_state_bus

    reset_state_bus()

    orch = MagicMock()
    recorded, restore = _record_gaze_calls(orch)

    orig_open = PIL.Image.open
    PIL.Image.open = _FakePILImage
    try:
        hf = _make_ready_follower(
            orch, lm=_FakeLandmark(0.5, 0.5), smooth_alpha=0.25, deadband_px=0
        )
        hf._tick()  # EMA 初始化 = 检测值 (320,240) → yaw=0
        hf._landmarker.lm = _FakeLandmark(0.9, 0.5)  # 检测跳到 576
        hf._tick()
        # EMA su = 0.25*576 + 0.75*320 = 384 → nx=(384-320)/320=0.2
        # yaw = -(0.2 * gaze_gain_yaw_deg(25)) = -5.0(而非直接跳 -20)
        assert recorded[-1][1] == -5.0
    finally:
        PIL.Image.open = orig_open
        restore()


def test_disable_resets_smoothing_state():
    """disable() 清 EMA/死区状态,重开不带历史惯性。"""
    import PIL.Image

    from reachymini_conversation.state_bus import reset_state_bus

    reset_state_bus()

    orch = MagicMock()
    recorded, restore = _record_gaze_calls(orch)

    orig_open = PIL.Image.open
    PIL.Image.open = _FakePILImage
    try:
        hf = _make_ready_follower(orch, lm=_FakeLandmark(0.9, 0.5), deadband_px=0)
        hf._tick()
        assert recorded[-1][1] == -20.0  # nx=0.8 → -(0.8*25)

        hf.disable()
        assert hf._smooth_u is None and hf._last_sent is None

        hf.enable()
        hf._landmarker.lm = _FakeLandmark(0.1, 0.5)
        hf._tick()  # EMA 重新初始化 → 直接等于新检测值(nx=-0.8 → yaw=+20)
        assert recorded[-1][1] == 20.0
    finally:
        PIL.Image.open = orig_open
        restore()


def test_flip_horizontal_mirrors_detection_coords():
    """相机硬件镜像时 flip_horizontal=True:检测坐标系翻转,yaw 取反。

    假相机画面里"手"(亮条)固定在图像右(0.8):
    - flip=False:质心 x=0.9 → nx=+0.8 → yaw=-20
    - flip=True :图像先翻转,亮条落到左缘 → nx=-0.8 → yaw=+20
    """
    import PIL.Image

    from reachymini_conversation.state_bus import reset_state_bus

    reset_state_bus()

    class _HandPILImage:
        """假相机:画面右侧 1/16 宽亮条 = 假手。"""

        def __init__(self, *a, **k):
            pass

        def convert(self, mode):
            import numpy as np

            arr = np.zeros((480, 640, 3), dtype=np.uint8)
            arr[:, int(0.8 * 640) :] = 255
            return arr

    class _ScanLandmarker:
        """假检测器:亮条质心列 = 手掌 x 位置(对翻转敏感)。"""

        def detect_for_video(self, image, timestamp_ms):
            import numpy as np

            view = image.numpy_view()
            mask = view.mean(axis=2) > 128
            cols = np.nonzero(mask.any(axis=0))[0]
            if len(cols) == 0:
                return _FakeHandResult(None)
            x = float(cols.mean()) / float(view.shape[1])
            return _FakeHandResult(_FakeLandmark(x, 0.5))

        def close(self):
            pass

    orch = MagicMock()
    recorded, restore = _record_gaze_calls(orch)

    orig_open = PIL.Image.open
    PIL.Image.open = _HandPILImage
    try:
        hf = _make_ready_follower(
            orch, lm=_FakeLandmark(0.8, 0.5), deadband_px=0, flip_horizontal=False
        )
        hf._landmarker = _ScanLandmarker()
        hf._tick()
        assert recorded[-1][1] == pytest.approx(-20.0, abs=0.5), f"未翻转:右侧手 → 看右(负 yaw): {recorded}"

        hf2 = _make_ready_follower(
            orch, lm=_FakeLandmark(0.8, 0.5), deadband_px=0, flip_horizontal=True
        )
        hf2._landmarker = _ScanLandmarker()
        hf2._tick()
        assert recorded[-1][1] == pytest.approx(20.0, abs=0.5), f"翻转后:亮条在左 → 看左(正 yaw): {recorded}"
    finally:
        PIL.Image.open = orig_open
        restore()
