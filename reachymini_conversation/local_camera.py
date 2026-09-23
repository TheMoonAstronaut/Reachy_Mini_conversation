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

logger = logging.getLogger(__name__)

# v4l2 by-id 名字特征(Sunplus 的 Reachy 眼睛相机)
DEVICE_ID_PATTERN = "Reachy_Mini_Camera"
# 输出为 JPEG bytes(首选 1080p 采集 + 管线内缩 720p,候选链见
# UsbEyeCamera._PIPELINE_CANDIDATES;分辨率经 caps 显式协商)
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
    """Reachy 眼睛 USB 相机的直连读取(get_frame_jpeg 供 MJPEG 推流用)。

    自愈(2026-09-16 用户实测):app 先启动、真机后插 USB 时,启动期的
    一次性探测已错过相机 → 画面永远空白只能重启 app。为此:
      - start() 后设备未就绪 → 保留 _want_running,get_frame_jpeg 被
        拉流时按 RETRY_INTERVAL_S 节流重试探测(支持热插拔);
      - 运行中拉流持续无帧(STALE_S)→ 判定设备被拔/掉线,重启管线。

    单抓帧线程(2026-09-17 P6 修复):原实现每个调用者各自阻塞拉 appsink
    新样本,MJPEG 流与手部跟随两个消费者会互相抢帧(每样本只被取走一次)。
    现改为唯一抓帧线程写 _last_jpeg 缓存,所有消费者读缓存(非阻塞、
    可安全共享);MJPEG 可能收到重复帧(浏览器可容忍)。
    """

    RETRY_INTERVAL_S = 3.0  # 未就绪时的重探测节流
    STALE_S = 5.0           # 运行中无帧判定掉线的阈值
    CAPTURE_HZ = 30.0       # 抓帧线程目标频率(相机实际帧率会进一步约束)

    # 视频流协商偏好(2026-09-18 帧率/延迟优化,两轮迭代):
    # 相机默认自协商到 4K(每帧 ~1MB,MJPEG 推给浏览器 ~110Mbps,WiFi 下
    # 延迟大、卡顿)。第一轮锁 1080p@60 直出(241KB/帧,110→27Mbps),本机
    # 流畅但局域网客户端(WiFi 双向空口争抢)仍延迟明显。
    # 第二轮:管线内 GStreamer jpegdec→videoscale→jpegenc 缩到 720p
    # (~100KB/帧),相机端 1080p60 解码+720p15 编码 CPU 开销可控(单核
    # ~25%,仅管线活着时)。副视角 UI 显示宽 ~570px、手部跟随检测降到
    # 640 宽,720p 绰绰有余。逐候选尝试,全部失败回退纯 image/jpeg
    # (旧 4K 行为),保证任何环境可跑。
    _PIPELINE_CANDIDATES = (
        # (v4l2src caps, 附加元素, appsink 前 caps)
        (
            "image/jpeg,width=1920,height=1080,framerate=60/1",
            "jpegdec ! videoscale ! jpegenc",
            "image/jpeg,width=1280,height=720",
        ),
        ("image/jpeg,width=1920,height=1080,framerate=60/1", "", ""),
        ("image/jpeg", "", ""),
    )

    def __init__(self, device: str | None = None) -> None:
        self._device = device  # None → 每次 start 时探测
        self._pipe = None
        self._appsink = None
        self._lock = threading.Lock()
        self._running = False
        self._want_running = False  # camera_stream start() 后即使没设备也保持"想跑"
        self._retry_at = 0.0        # 下次允许重探测的 monotonic 时刻
        self._last_frame_at = 0.0   # 最近一次拉到帧的时刻
        self._last_jpeg: bytes | None = None  # 抓帧线程写的最新帧缓存
        self._cap_thread: threading.Thread | None = None
        self._cap_stop = threading.Event()

    # ---------- camera_stream 生命周期钩子 ----------
    def start(self) -> None:
        """启动 GStreamer 管线(相机不在时保留重试意愿,走占位图)。"""
        with self._lock:
            self._want_running = True
            if self._running:
                return
            device = self._device or find_reachy_camera()
            if device is None:
                logger.info("[usb-eye] 未找到 Reachy 相机(未插真机?保持重试)")
                return
            try:
                self._open_pipeline(device)
                self._running = True
                self._last_frame_at = time.monotonic()
                logger.info(f"[usb-eye] 已开 {device}")
            except Exception as e:
                logger.warning(f"[usb-eye] 打开 {device} 失败: {type(e).__name__}: {e}")
                self._close_pipeline()
        # 抓帧线程在锁外拉起(线程自身会按需取锁,避免持锁期间线程竞争)
        if self._running:
            self._ensure_capture_thread()

    def stop(self) -> None:
        self._cap_stop.set()
        if self._cap_thread is not None:
            self._cap_thread.join(timeout=2.0)
            self._cap_thread = None
        with self._lock:
            self._want_running = False
            self._close_pipeline()
            self._running = False
            self._last_jpeg = None
            logger.info("[usb-eye] 已停止")

    # ---------- camera_stream 数据接口 ----------
    def get_frame_jpeg(self) -> bytes | None:
        """读最新一帧缓存(非阻塞;相机未就绪/无新帧返回 None;内置热插拔自愈)。

        语义(2026-09-17):不再直接拉 appsink(那会与其他消费者抢帧),只读
        抓帧线程维护的缓存。缓存帧可能重复,消费者按需自行去重。
        """
        if not self._running or self._appsink is None:
            # 自愈路径 1:start() 时设备不在 → 节流重探测(真机后插场景)
            if self._want_running and time.monotonic() >= self._retry_at:
                self._retry_at = time.monotonic() + self.RETRY_INTERVAL_S
                logger.debug("[usb-eye] 重探测 Reachy 相机…")
                self.start()
            return None
        return self._last_jpeg

    # ---------- 内部 ----------
    def _ensure_capture_thread(self) -> None:
        """拉起唯一抓帧线程(幂等)。"""
        if self._cap_thread is not None and self._cap_thread.is_alive():
            return
        self._cap_stop.clear()
        self._cap_thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="usb-eye-capture"
        )
        self._cap_thread.start()
        logger.info(f"[usb-eye] 抓帧线程已启动({self.CAPTURE_HZ:.0f} Hz)")

    def _capture_loop(self) -> None:
        """唯一 appsink 消费者:拉帧写缓存;无帧时走掉线自愈。"""
        while not self._cap_stop.wait(1.0 / self.CAPTURE_HZ):
            self._pull_once()

    def _pull_once(self) -> bool:
        """从 appsink 拉一帧写缓存(供抓帧线程与测试复用)。返回是否拉到帧。"""
        if not self._running or self._appsink is None:
            return False
        if not hasattr(self._appsink, "emit"):  # 测试假对象,无 GST 接口
            return False
        try:
            sample = self._appsink.emit("try-pull-sample", 50_000_000)  # 50ms 超时
        except Exception as e:
            logger.debug(f"[usb-eye] pull 异常: {e}")
            return False
        if sample is None:
            self._maybe_restart_stale()
            return False
        buf = sample.get_buffer()
        ok, map_info = buf.map(self._GST.MapFlags.READ)
        if not ok:
            return False
        try:
            self._last_jpeg = bytes(map_info.data)
            self._last_frame_at = time.monotonic()
            return True
        finally:
            buf.unmap(map_info)

    def _maybe_restart_stale(self) -> None:
        """运行中持续无帧 → 设备可能被拔/掉线 → 节流重启管线。"""
        now = time.monotonic()
        if now - self._last_frame_at < self.STALE_S:
            return
        if now < self._retry_at:
            return
        self._retry_at = now + self.RETRY_INTERVAL_S
        logger.warning("[usb-eye] 持续无帧,判定掉线,重启管线")
        try:
            self._close_pipeline()
            self._running = False
            self.start()  # 重新探测设备并重开(会复用/重拉抓帧线程)
        except Exception as e:
            logger.warning(f"[usb-eye] 重启管线失败: {type(e).__name__}: {e}")

    def _open_pipeline(self, device: str) -> None:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        self._GST = Gst
        # MJPEG 直出(或轻量重编码):appsink 拿到的都是 JPEG bytes。
        # 按 _PIPELINE_CANDIDATES 顺序协商(首选 1080p 相机 + 管线内缩
        # 720p),parse_launch/PLAYING 失败自动回退下一候选,全失败抛错
        # 交 start() 的自愈重试。
        last_err: Exception | None = None
        for src_caps, middle, sink_caps in self._PIPELINE_CANDIDATES:
            parts = [f'v4l2src device="{device}" ! {src_caps}']
            if middle:
                parts.append(f"! {middle}")
            if sink_caps:
                parts.append(f"! {sink_caps}")
            parts.append("! appsink name=eye-sink drop=true max-buffers=2 sync=false")
            desc = " ".join(parts)
            try:
                self._pipe = Gst.parse_launch(desc)
                self._appsink = self._pipe.get_by_name("eye-sink")
                ret = self._pipe.set_state(Gst.State.PLAYING)
                if ret == Gst.StateChangeReturn.FAILURE:
                    raise RuntimeError(f"pipeline 启动失败({src_caps})")
                logger.info(f"[usb-eye] 管线已开 {device}(方案: {src_caps} | {middle or '直出'})")
                return
            except Exception as e:
                last_err = e
                logger.warning(f"[usb-eye] 方案({src_caps} | {middle or '直出'})协商失败: {e}")
                self._close_pipeline()
        raise RuntimeError(f"所有管线候选均失败: {last_err}")

    def _close_pipeline(self) -> None:
        if self._pipe is not None:
            try:
                self._pipe.set_state(self._GST.State.NULL)
            except Exception:
                pass
        self._pipe = None
        self._appsink = None
