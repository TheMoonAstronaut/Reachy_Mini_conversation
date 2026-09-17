"""UsbEyeCamera 热插拔自愈测试(local_camera)。

保护的事(2026-09-16 用户实测):app 先启动、真机后插 USB → 启动期一次性
探测错过相机 → 画面永远空白只能重启 app。修复后:
  1. start() 时无设备 → 保留重试意愿,get_frame_jpeg 节流重探测;
  2. 重试成功 → 打开管线出帧;
  3. stop() 后不再重试;
  4. 运行中持续无帧 → 判定掉线重启管线。

不碰真 GStreamer:mock find_reachy_camera + _open_pipeline(_open_pipeline
内部才 import gi,被整体替换)。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from reachymini_conversation.local_camera import UsbEyeCamera  # noqa: E402


def _cam_with_fake_pipeline(device: str | None = None):
    """构造相机:假 _open_pipeline(只记调用),不 import gi。"""
    cam = UsbEyeCamera(device=device)
    opened: list[str] = []

    def _fake_open(dev: str) -> None:
        opened.append(dev)
        cam._pipe = object()  # 非 None 即可
        cam._appsink = object()

    cam._open_pipeline = _fake_open  # type: ignore[method-assign]
    return cam, opened


class TestStartWithoutDeviceRetries:
    def test_start_no_device_keeps_retry_willingness(self):
        cam, opened = _cam_with_fake_pipeline()
        with mock.patch(
            "reachymini_conversation.local_camera.find_reachy_camera",
            return_value=None,
        ):
            cam.start()
        assert not cam._running
        assert cam._want_running, "start 后无设备也必须保留重试意愿(自愈前提)"

    def test_get_frame_retries_and_opens_when_device_appears(self):
        cam, opened = _cam_with_fake_pipeline()
        with mock.patch(
            "reachymini_conversation.local_camera.find_reachy_camera",
            return_value=None,
        ):
            cam.start()
        assert cam.get_frame_jpeg() is None  # 第一次拉流 → 触发重探测(仍无设备)

        # 模拟后插真机:重探测发现设备 → 打开管线
        # (第一次拉流已把 _retry_at 推到 3s 后,这里直接重置计时,测的是
        # "重探测到设备"本身而非节流间隔——节流由 test_retry_is_throttled 守护)
        cam._retry_at = 0.0
        with mock.patch(
            "reachymini_conversation.local_camera.find_reachy_camera",
            return_value="/dev/v4l/by-id/usb-Reachy_Mini_Camera-index0",
        ):
            assert cam.get_frame_jpeg() is None  # 本次触发重试
        assert cam._running, "重探测到设备后必须进入运行态"
        assert len(opened) == 1

    def test_retry_is_throttled(self):
        cam, opened = _cam_with_fake_pipeline()
        with mock.patch(
            "reachymini_conversation.local_camera.find_reachy_camera",
            return_value=None,
        ):
            cam.start()
        cam._retry_at = time.monotonic() + 60  # 模拟刚重试过
        with mock.patch(
            "reachymini_conversation.local_camera.find_reachy_camera",
            return_value="/dev/fake",
        ):
            cam.get_frame_jpeg()
        assert not opened, "节流期内不得重复重探测"

    def test_stop_clears_retry_willingness(self):
        cam, opened = _cam_with_fake_pipeline()
        with mock.patch(
            "reachymini_conversation.local_camera.find_reachy_camera",
            return_value=None,
        ):
            cam.start()
        cam.stop()
        assert not cam._want_running
        cam.get_frame_jpeg()
        assert not opened, "stop() 后不得再重试"


class TestStaleRestart:
    def test_stale_pipeline_restarts(self):
        cam, opened = _cam_with_fake_pipeline(device="/dev/fake0")
        cam.start()
        assert cam._running and len(opened) == 1
        # 模拟持续无帧超过 STALE_S
        cam._appsink = object()  # 仍在运行态
        cam._last_frame_at = time.monotonic() - (UsbEyeCamera.STALE_S + 1)
        cam._retry_at = 0.0

        # pull 需要 appsink.emit;用假 appsink 返回 None
        class _FakeSink:
            def emit(self, *_a):
                return None

        cam._appsink = _FakeSink()
        cam._GST = type("G", (), {"MapFlags": type("M", (), {"READ": 0})})()
        assert cam.get_frame_jpeg() is None
        assert len(opened) == 2, "持续无帧必须重启管线(掉线自愈)"
