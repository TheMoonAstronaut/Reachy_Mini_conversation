"""utils.scene_stream — MuJoCo 场景流(studio_close 相机)UDP 接收端。

链路(配合 daemon_launcher 的方案 B patch):
    daemon 侧:MujocoBackend.rendering_loop("studio_close", 5006)
        → GStreamerUDPCamera(appsrc → rtpvrawpay → udpsink 127.0.0.1:5006)
    app 侧(本模块):udpsrc:5006 → rtpvrawdepay → videoconvert → jpegenc → appsink
        → 最新 JPEG 帧缓冲 → camera_stream.py 的 /scene_feed(MJPEG)

接收 caps 的依据:
    与 SDK media_server.py `_build_sim_source()`(消费同型号发送端的 5005 眼睛流)
    完全一致 —— 发送端 caps 见 SDK gstreamer_udp_camera.py:
    video/x-raw,format=RGB,...,framerate=25/1 → rtpvrawpay(mtu 1400)
    → application/x-rtp,payload=96。
    所以这里:application/x-rtp,media=video,clock-rate=90000,encoding-name=RAW,
    sampling=RGB,depth=8,width=640,height=640,payload=96。

降级语义(与 camera_stream 的占位机制对接):
    - gi/Gst 不可用、元素缺失、管线启动失败 → start() 返回 False,只记日志,
      get_frame_jpeg() 永远返回 None → /scene_feed 自动走占位图,不 crash。
    - 运行中断流 → 探针拿不到帧 → /scene_feed_status available 翻 false。
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# 场景流默认参数:与 daemon_launcher.SCENE_UDP_PORT / SDK CAMERA_SIZES 对齐
DEFAULT_SCENE_PORT = 5006
DEFAULT_SCENE_WIDTH = 640
DEFAULT_SCENE_HEIGHT = 640

# 接收管线(parse_launch 语法);caps 与 SDK media_server._build_sim_source 一致
_SCENE_PIPELINE_TEMPLATE = (
    "udpsrc port={port} "
    'caps="application/x-rtp,media=(string)video,clock-rate=(int)90000,'
    "encoding-name=(string)RAW,sampling=(string)RGB,depth=(string)8,"
    'width=(string){width},height=(string){height},payload=(int)96" '
    "! queue "
    "! rtpvrawdepay "
    "! videoconvert "
    "! jpegenc "
    '! appsink name=scene_sink max-buffers=2 drop=true emit-signals=false'
)


class SceneUdpReceiver:
    """GStreamer UDP → 最新 JPEG 帧的接收器(线程安全)。

    用法:
        receiver = SceneUdpReceiver(port=5006)
        ok = receiver.start()           # False = 优雅降级(占位图)
        jpeg = receiver.get_frame_jpeg()  # None = 暂无帧
        receiver.stop()
    """

    def __init__(
        self,
        port: int = DEFAULT_SCENE_PORT,
        width: int = DEFAULT_SCENE_WIDTH,
        height: int = DEFAULT_SCENE_HEIGHT,
    ) -> None:
        self.port = port
        self.width = width
        self.height = height
        self._pipeline = None
        self._stop = threading.Event()
        self._pull_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self.started: bool = False  # start() 是否成功(供状态/排障)

    # ---------- 生命周期 ----------
    def start(self) -> bool:
        """构建并启动接收管线。任何失败都 log + 返回 False,不抛异常。"""
        if self.started:
            return True
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst

            Gst.init(None)
        except Exception as e:  # gi 缺失等
            logger.warning(f"[scene-stream] gi/Gst 不可用,场景流禁用: {e}")
            return False

        pipeline_desc = _SCENE_PIPELINE_TEMPLATE.format(
            port=self.port, width=self.width, height=self.height
        )
        try:
            self._pipeline = Gst.parse_launch(pipeline_desc)
        except Exception as e:  # 元素缺失 / caps 语法错误 → GLib.Error
            logger.warning(
                f"[scene-stream] 接收管线创建失败(缺 GStreamer 元素?),"
                f"场景流走占位图: {e}\n  pipeline: {pipeline_desc}"
            )
            self._pipeline = None
            return False

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            logger.warning("[scene-stream] 接收管线无法进入 PLAYING,场景流走占位图")
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
            return False

        self._stop.clear()
        self._pull_thread = threading.Thread(
            target=self._pull_loop,
            args=(Gst,),
            daemon=True,
            name="scene-udp-pull",
        )
        self._pull_thread.start()
        self.started = True
        logger.info(
            f"[scene-stream] 场景流接收已启动:UDP:{self.port} "
            f"({self.width}x{self.height})"
        )
        return True

    def stop(self) -> None:
        """停止管线与拉帧线程(幂等)。"""
        self._stop.set()
        if self._pull_thread is not None and self._pull_thread.is_alive():
            self._pull_thread.join(timeout=1.0)
        self._pull_thread = None
        if self._pipeline is not None:
            try:
                self._pipeline.set_state(self._get_gst_state_null())
            except Exception:
                pass
            self._pipeline = None
        self.started = False
        logger.info("[scene-stream] 场景流接收已停止")

    def _get_gst_state_null(self):  # 小工具:避免模块级持有 Gst
        from gi.repository import Gst

        return Gst.State.NULL

    # ---------- 拉帧线程 ----------
    def _pull_loop(self, gst) -> None:
        """持续从 appsink 拉最新 JPEG 帧,覆盖式写入缓冲(只保留最新一帧)。"""
        sink = self._pipeline.get_by_name("scene_sink")
        if sink is None:
            logger.warning("[scene-stream] appsink 'scene_sink' 不存在,拉帧线程退出")
            return
        while not self._stop.is_set():
            try:
                # try-pull-sample(timeout_ns):无帧时 200ms 后返回 None(不阻塞死)
                sample = sink.emit("try-pull-sample", 200_000_000)
            except Exception as e:
                logger.debug(f"[scene-stream] pull-sample 异常: {e}")
                break
            if sample is None:
                continue
            buf = sample.get_buffer()
            data = buf.extract_dup(0, buf.get_size())
            if data:
                with self._lock:
                    self._latest_jpeg = data

    # ---------- 与 camera_stream 探针/MJPEG 机制对接的读接口 ----------
    def get_frame_jpeg(self) -> bytes | None:
        """返回最新一帧 JPEG bytes;无帧(未启动/断流)返回 None。"""
        with self._lock:
            return self._latest_jpeg
