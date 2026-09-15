"""reachymini_conversation.local_camera — 有线真机眼睛 USB 相机直连(V2 Fix D)。

为什么不走 daemon B 的媒体链:
  daemon B 以 --no-media 运行(避免与 sim daemon 抢 unixfd socket / UDP 5005 /
  WebRTC 8443)。但有线版真机的眼睛相机就是**本机的 USB UVC 相机**
  (Sunplus "Reachy Mini Camera"),支持 MJPEG 直出 —— app 进程开 v4l2 直接读,
  appsink 拿到的就是 JPEG bytes,零解码转码,延迟最低。

用法(供 camera_stream 的 real_frame_provider):
    cam = UsbEyeCamera()
    jpeg = cam.get_frame_jpeg()   # bytes | None(相机不在线/未插)
    # 生命周期由 camera_stream lifespan 托管(start/stop)

设备选择:
  按 /dev/v4l/by-id/ 名字匹配(含 "Reachy_Mini_Camera"),避免误开笔记本摄像头;
  找不到时所有调用返回 None(UI 走占位图,不 crash)。
"""

from __future__ import annotations

import glob
import logging
import threading
import time

import numpy as np

logger = logging.getLogger(__name__)

# v4l2 by-id 名字特征(Sunplus 的 Reachy 眼睛相机)
DEVICE_ID_PATTERN = "Reachy_Mini_Camera"
# MJPEG 直出,浏览器 <img>/MJPEG 链零转码;分辨率取相机自协商(1080p MJPEG@60 在列)
PIPELINE_FMT = "image/jpeg"


def find_reachy_camera() -> str | None:
    """按 by-id 名字找 Reachy 眼睛相机的 /dev/video 节点。"""
    for path in sorted(glob.glob("/dev/v4l/by-id/*Reachy*Camera*-index0")):
        return path
    for path in sorted(glob.glob("/dev/v4l/by-id/*Reachy*")):
        if "index0" in path:
            return path
    return None


class UsbEyeCamera:
    """Reachy 眼睛 USB 相机的直连读取(get_frame_jpeg 供 MJPEG 推流用)。"""

    def __init__(self, device: str | None = None) -> None:
        self._device = device  # None → 每次 start 时探测
        self._pipe = None
        self._appsink = None
        self._lock = threading.Lock()
        self._running = False

    # ---------- camera_stream 生命周期钩子 ----------
    def start(self) -> None:
        """启动 GStreamer 管线(相机不在时静默失败,走占位图)。"""
        with self._lock:
            if self._running:
                return
            device = self._device or find_reachy_camera()
            if device is None:
                logger.info("[usb-eye] 未找到 Reachy 相机(未插真机?)")
                return
            try:
                self._open_pipeline(device)
                self._running = True
                logger.info(f"[usb-eye] 已开 {device}(MJPEG 直出)")
            except Exception as e:
                logger.warning(f"[usb-eye] 打开 {device} 失败: {type(e).__name__}: {e}")
                self._close_pipeline()

    def stop(self) -> None:
        with self._lock:
            self._close_pipeline()
            self._running = False
            logger.info("[usb-eye] 已停止")

    # ---------- camera_stream 数据接口 ----------
    def get_frame_jpeg(self) -> bytes | None:
        """拉最新一帧 JPEG(相机未就绪/无新帧返回 None)。"""
        if not self._running or self._appsink is None:
            return None
        try:
            sample = self._appsink.emit("try-pull-sample", 100_000_000)  # 100ms 超时
            if sample is None:
                return None
            buf = sample.get_buffer()
            ok, map_info = buf.map(self._GST.MapFlags.READ)
            if not ok:
                return None
            try:
                return bytes(map_info.data)
            finally:
                buf.unmap(map_info)
        except Exception as e:
            logger.debug(f"[usb-eye] pull 异常: {e}")
            return None

    # ---------- 内部 ----------
    def _open_pipeline(self, device: str) -> None:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        self._GST = Gst
        # MJPEG 直出:appsink 拿到即 JPEG,无需 jpegdec
        desc = (
            f'v4l2src device="{device}" ! {PIPELINE_FMT} '
            f"! appsink name=eye-sink drop=true max-buffers=2 sync=false"
        )
        self._pipe = Gst.parse_launch(desc)
        self._appsink = self._pipe.get_by_name("eye-sink")
        ret = self._pipe.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("pipeline 启动失败")

    def _close_pipeline(self) -> None:
        if self._pipe is not None:
            try:
                self._pipe.set_state(self._GST.State.NULL)
            except Exception:
                pass
        self._pipe = None
        self._appsink = None
