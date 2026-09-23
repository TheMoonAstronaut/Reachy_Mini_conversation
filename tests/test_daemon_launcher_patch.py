"""daemon_launcher 方案 B monkey-patch 的 guard 测试(纯 mock,不打真 gst/daemon)。

保护的三件事(对应 daemon_launcher.py 模块 docstring):
  1. rendering_loop 替换实现:构造 GStreamerUDPCamera 时必须传 dest_port=port
     (SDK 1.10.0 原行为:port 参数被忽略,永远 5005)。
  2. run 包装:无条件起 studio_close→5006 场景渲染线程;headless=True 时
     额外补 eye_camera→5005 渲染线程;headless=False 时不起(原 run 自己起)。
  3. patch 幂等:重复 patch 不会叠包(原 run 仍只被调一次)。

不 import mujoco / gi / 真 SDK backend —— 注入 fake 模块,CI 也能跑。
"""

from __future__ import annotations

import logging
import sys
import threading
import types
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from reachymini_conversation import daemon_launcher  # noqa: E402


class _FakeRenderer:
    """假 mujoco.Renderer:update_scene/render 都是 no-op。"""

    def update_scene(self, data, camera_id) -> None:
        pass

    def render(self):
        return b"\x00" * 12  # send_frame 已被 mock,内容无所谓


class _FakeMujocoBackend:
    """假 MujocoBackend:有 SDK 同名接口(run/should_stop/_get_renderer/...)。"""

    def __init__(self, headless: bool) -> None:
        self.headless = headless
        self.should_stop = threading.Event()
        self.logger = logging.getLogger("test.launcher")
        self.rendering_timestep = 0.0
        self.data = object()
        self.run_calls = 0

    def run(self) -> str:
        """原 SDK run 的替身:记录被调次数,返回标记值验证透传。"""
        self.run_calls += 1
        return "orig-run-ran"

    def _get_renderer(self, camera_name: str):
        return _FakeRenderer()

    def _get_camera_id(self, camera_name: str):
        return 0


def _make_fake_module() -> types.SimpleNamespace:
    """patch 目标:模拟 reachy_mini.daemon.backend.mujoco.backend 模块。"""
    return types.SimpleNamespace(MujocoBackend=_FakeMujocoBackend)


def _fake_gst_sender_module(captured: dict, backend: _FakeMujocoBackend):
    """假 GStreamerUDPCamera 模块:构造时捕获 kwargs,send_frame 一帧后停循环。"""
    fake_mod = types.ModuleType("reachy_mini.media.gstreamer_udp_camera")

    class FakeSender:
        def __init__(self, dest_port=5005, width=None, height=None, log_level=None):
            captured.update(
                dest_port=dest_port, width=width, height=height, log_level=log_level
            )

        def start(self) -> None:
            pass

        def send_frame(self, im) -> None:
            backend.should_stop.set()  # 送一帧即退出 while 循环

    fake_mod.GStreamerUDPCamera = FakeSender
    return fake_mod


def test_rendering_loop_passes_dest_port_to_gstreamer():
    """核心 guard:rendering_loop 替换实现必须把 port 传给 GStreamerUDPCamera。

    回归保护:SDK 1.10.0 backend.py L143-147 构造时不传 dest_port,
    `rendering_loop("studio_close", 5006)` 实际仍发到 5005,场景流永远到不了 app。
    """
    module = _make_fake_module()
    daemon_launcher.patch_mujoco_backend(module)

    backend = module.MujocoBackend(headless=True)
    captured: dict = {}
    fake_gst = _fake_gst_sender_module(captured, backend)
    with mock.patch.dict(
        sys.modules, {"reachy_mini.media.gstreamer_udp_camera": fake_gst}
    ):
        backend.rendering_loop("studio_close", 5006)

    assert captured["dest_port"] == 5006, (
        f"dest_port 未正确传递(SDK port 忽略 bug 复发?): {captured}"
    )
    # studio_close 必须是 640x640(SDK CAMERA_SIZES 定义)
    assert (captured["width"], captured["height"]) == (640, 640)


def test_run_spawns_scene_thread_and_eye_thread_when_headless():
    """headless=True:run 包装必须起两路渲染线程(scene:5006 + eye:5005)。"""
    module = _make_fake_module()
    daemon_launcher.patch_mujoco_backend(module)

    backend = module.MujocoBackend(headless=True)
    spawned: list = []  # (target, kwargs)

    class _MockThread:
        def __init__(self, target=None, **kwargs):
            spawned.append((target, kwargs))

        def start(self) -> None:
            pass

    rendering_calls: list[tuple[str, int]] = []
    backend.rendering_loop = lambda cam, port: rendering_calls.append((cam, port))

    with mock.patch.object(daemon_launcher, "Thread", _MockThread):
        result = backend.run()

    assert result == "orig-run-ran", "包装必须透传原 run() 的返回"
    assert backend.run_calls == 1, "原 run() 应恰好被调一次"

    names = sorted(kw.get("name", "") for _, kw in spawned)
    assert names == ["render-eye", "render-scene"], (
        f"headless 下应起 scene+eye 两路线程,实际: {names}"
    )
    assert all(kw.get("daemon") is True for _, kw in spawned)

    # 执行捕获的线程 target,验证 (camera, port) 参数
    for target, _ in spawned:
        target()
    assert sorted(rendering_calls) == [("eye_camera", 5005), ("studio_close", 5006)]


def test_run_spawns_only_scene_thread_when_not_headless():
    """headless=False:只起 scene:5006;eye 流由 SDK 原 run 自己起(不能重复起)。"""
    module = _make_fake_module()
    daemon_launcher.patch_mujoco_backend(module)

    backend = module.MujocoBackend(headless=False)
    spawned: list = []

    class _MockThread:
        def __init__(self, target=None, **kwargs):
            spawned.append((target, kwargs))

        def start(self) -> None:
            pass

    with mock.patch.object(daemon_launcher, "Thread", _MockThread):
        backend.run()

    names = [kw.get("name", "") for _, kw in spawned]
    assert names == ["render-scene"], (
        f"非 headless 只应起 scene 线程(eye 由 SDK 原 run 负责),实际: {names}"
    )
    assert backend.run_calls == 1


def test_patch_is_idempotent():
    """重复 patch 不叠包:原 run 仍只被调一次,渲染线程不翻倍。"""
    module = _make_fake_module()
    daemon_launcher.patch_mujoco_backend(module)
    daemon_launcher.patch_mujoco_backend(module)  # 第二次应直接跳过

    backend = module.MujocoBackend(headless=True)
    with mock.patch.object(daemon_launcher, "Thread") as mock_thread:
        backend.run()

    assert backend.run_calls == 1, "patch 叠包会导致原 run 被调多次"
    assert mock_thread.call_count == 2, "patch 叠包会导致线程重复 spawn"


def test_rendering_thread_exception_does_not_crash_daemon():
    """渲染线程内异常(GStreamer 缺失等)只记日志,不影响原 run 流程。"""
    module = _make_fake_module()
    daemon_launcher.patch_mujoco_backend(module)

    backend = module.MujocoBackend(headless=True)

    def _boom(cam, port):
        raise RuntimeError("no gst")

    backend.rendering_loop = _boom

    # 用真 Thread:guarded target 必须吞掉异常
    with mock.patch.object(daemon_launcher, "Thread", wraps=threading.Thread) as t:
        backend.run()
        for call in t.call_args_list:
            target = call.kwargs.get("target") or call.args[0]
            target()  # 不应抛异常
    assert backend.run_calls == 1


# ---------- sim 音频 sink patch(USB 真机插着时 sim 声音走电脑音箱) ----------


class _FakeMediaModule:
    """假媒体模块:有 SDK 同名的 get_audio_device(记录调用)。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_audio_device(self, device_type: str = "Source"):
        self.calls.append(device_type)
        return "usb-reachy-card-id"  # 模拟 SDK 找到真机声卡


def test_audio_sink_patch_routes_sink_to_default_output():
    """核心 guard:patch 后 Sink 查询必须返回 None(SDK 回落 autoaudiosink)。

    回归(2026-09-16 用户实测):USB 连真机时 sim 模式 TTS 从真机喇叭出来。
    根因是 SDK 按名字匹配 "Reachy Mini Audio" 做 Sink。
    """
    mod = _FakeMediaModule()
    daemon_launcher.patch_sim_audio_sink([mod])

    assert mod.get_audio_device("Sink") is None, (
        "patch 后 Sink 不得再命中真机声卡(sim 声音必须走 PC 默认输出)"
    )
    assert mod.get_audio_device("Source") == "usb-reachy-card-id", (
        "Source 必须透传原名匹配(不得影响真机麦采集)"
    )


def test_audio_sink_patch_is_idempotent():
    """重复 patch 不叠包:Source 透传仍指原函数,Sink 仍返回 None。"""
    mod = _FakeMediaModule()
    daemon_launcher.patch_sim_audio_sink([mod])
    first = mod.get_audio_device
    daemon_launcher.patch_sim_audio_sink([mod])
    assert mod.get_audio_device is first, "patch 叠包:第二次应直接跳过"
