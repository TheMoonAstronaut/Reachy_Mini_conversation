"""reachymini_conversation.hand_follower — 手部跟随(决策 5,P6)。

基于 MediaPipe 1.0+ tasks API `HandLandmarker`:
  - 后台线程读摄像头帧
  - 识别手掌中心(landmark 9)
  - 调 Reachy `look_at_image(u, v)` 让头部跟随
  - 默认关(决策 5:LLM 工具启停),UI 按钮显式开

降级:
  - 无摄像头(sim 模式 get_frame_jpeg 返回 None 或异常)
  - 模型文件没下(hand_landmarker.task)
  - mediapipe 没装
  → 后台线程优雅降级,UI 显"不可用"

动作仲裁(2026-09-17 P6 部署定案,经 state_bus 协调,无新依赖):
  - Reachy 播报(status=speaking/playing)时跟随让位 —— 头部归音频 wobbler;
  - 跟随生效(开关开 + 检测到手)时空闲呼吸不新起段 —— 头部归跟随;
  - 与声源跟随同开时无互斥(两者皆默认关),UI 文案提示勿同开。

头部驱动(2026-09-17 重构,09-18 两次修正):
  A. 不用 SDK look_at_image,改为"像素偏移 → yaw/pitch 线性映射"
     (原因见下);经 MirrorOrchestrator 一次调用镜像 sim + real。
  B. 用 set_target(15Hz 流式发送 EMA 平滑后的目标姿态),**不用**
     goto_target —— SDK goto_target 是同步阻塞实现(client 侧
     wait_for_task_completion 整整 duration 秒!),镜像双实例串行
     后每次指令冻结事件循环 ~0.6s、等效更新率仅 ~1.6Hz,这就是用户
     实测"一卡一卡"的根因(2026-09-18 实锤)。set_target fire-and-
     forget,daemon 伺服直接追踪流式目标,配合 EMA 增量小步进 = 丝滑。
  C. 不用 look_at_image 的原因(对比官方实现后的结论):
     - 有线模式 daemon B 以 --no-media 运行 → 真机 SDK look_at_image
       必抛 "Camera is not initialized"(它要求 daemon 相机就绪);
     - look_at_image 内部 assert u/v 在"daemon 那路相机"的分辨率内,
       我们的帧来自 4K USB 相机,坐标喂给 sim(低分辨率)会越界;
     - 官方无 Reachy Mini 手部跟随参考实现(pollen-robotics 下无此
       仓库),归一化偏移 + 可调增益是社区通用做法。

符号约定(2026-09-18 从 SDK 源码推导,勿凭直觉改):
  reachy 头部系由 look_at.py DEFAULT_HEAD_TO_CAMERA_TRANSFORM 定义:
  X=前、Y=图像左、Z=图像上;create_head_pose 用 R.from_euler("xyz")。
  推得:正 pitch = 低头,正 yaw = 转向图像左。因此:
    yaw_cmd   = -nx * gain   (手在图像右 → nx>0 → 负 yaw = 看右 ✓)
    pitch_cmd = +ny * gain   (手在图像下 → ny>0 → 正 pitch = 看下 ✓)
  2026-09-17 首版两符号皆反(用户实测"手往上头往下、左变右")。

性能(2026-09-18):检测前降采样——4K 全尺寸 PIL 解码实测 69ms/帧导致
tick 超时卡顿;PIL draft 对 MJPEG 实测不生效;改用 cv2.imdecode 的
IDCT 层 1/4 降采样(960x540,~11ms)再缩到 640 宽检测(~8ms),
单帧 ~19ms ≪ 15Hz 预算 67ms。cv2 是 mediapipe 的传递依赖,失败回退 PIL。

模型下载(用户首次使用):
  - https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task
  - 保存到 ~/.cache/reachymini/hand_landmarker.task(或环境变量指定)
  - P6 不自动下载,留给用户
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# 默认模型路径(用户首次跑会下载)
DEFAULT_MODEL_PATH = Path.home() / ".cache" / "reachymini" / "hand_landmarker.task"

# MediaPipe 可用性探测缓存(on-robot 刚需,见 _mediapipe_usable)
_MP_PROBE_MARKER = Path.home() / ".cache" / "reachymini" / "mp_probe_ok"
# 失败也缓存:CM4 上每次启动重跑探测(子进程 import mediapipe + 创建/销毁
# landmarker ≈15s 且内存峰值大)浪费资源且徒增 OOM 风险;失败后同样落标记,
# 除非模型/环境变更(手动删标记文件)不再重试。
_MP_PROBE_FAILED_MARKER = Path.home() / ".cache" / "reachymini" / "mp_probe_failed"


def _mediapipe_probe_mark_failed() -> None:
    _MP_PROBE_FAILED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    _MP_PROBE_FAILED_MARKER.touch()


def _mediapipe_usable(model_path: Path) -> bool:
    """子进程预检 MediaPipe HandLandmarker 能否在本机创建。

    为什么需要(2026-09-20 树莓派 CM4 实测):mediapipe 1.0.1 的 aarch64
    wheel 内原生库按 AES 指令编译(go sigill-fail-fast / armv8 crypto
    intrinsics),而 CM4(BCM2711)未实现 ARMv8 crypto 扩展 ——
    create_from_options 经 ctypes 加载 .so 时整个进程被 SIGILL 炸掉。
    SIGILL 是进程级致命信号,try/except 无法捕获,会把整个 app 拖死;
    而手部跟随是可选增强,绝不许拖死主应用。故:
      - 首次启动用子进程跑完整创建流程,探活结果缓存到 mp_probe_ok /
        mp_probe_failed 标记文件(成功与失败都缓存,以后启动秒过,
        不重跑探测;删除标记文件可强制重探);
      - 探测失败(非零返回/超时)→ 判定不可用,HandFollower 优雅降级
        (available=False,UI 显"不可用"),app 其余功能不受影响。
    """
    if _MP_PROBE_MARKER.exists():
        return True
    if _MP_PROBE_FAILED_MARKER.exists():
        return False
    import subprocess
    import sys

    code = (
        "import mediapipe as mp;"
        f"opts = mp.tasks.vision.HandLandmarkerOptions("
        f"base_options=mp.tasks.BaseOptions(model_asset_path={str(model_path)!r}),"
        "running_mode=mp.tasks.vision.RunningMode.VIDEO, num_hands=1);"
        "mp.tasks.vision.HandLandmarker.create_from_options(opts).close();"
        "print('PROBE_OK')"
    )
    try:
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            timeout=180,
        )
        if r.returncode == 0 and b"PROBE_OK" in r.stdout:
            _MP_PROBE_MARKER.parent.mkdir(parents=True, exist_ok=True)
            _MP_PROBE_MARKER.touch()
            return True
        logger.warning(
            f"[HandFollower] MediaPipe 探测失败(rc={r.returncode}): "
            f"{r.stderr[-200:]!r}"
        )
    except Exception as e:
        logger.warning(f"[HandFollower] MediaPipe 探测异常: {e}")
    _mediapipe_probe_mark_failed()
    return False


class HandFollower:
    """手部跟随:MediaPipe HandLandmarker → look_at_image。

    用法:
        hf = HandFollower(
            orchestrator=mirror_orch,
            get_frame_jpeg_fn=reachy.media.get_frame_jpeg,
            poll_hz=15.0,
        )
        hf.start()          # 后台线程
        hf.enable()         # 默认关,显式开
        # ... 用户挥手,头部跟随
        hf.disable()
        hf.stop()
    """

    # Reachy 自己播报时头部由音频 wobbler 驱动("特别动作"),跟随必须让位,
    # 否则 look_at_image 与 wobbler 同写头部打架(2026-09-17 P6 部署仲裁)。
    _YIELD_STATES = frozenset({"speaking", "playing"})

    def __init__(
        self,
        orchestrator: Any,
        get_frame_jpeg_fn: Any | None = None,
        poll_hz: float = 15.0,
        model_path: str | None = None,
        landmark_index: int = 9,  # 中指 MCP = 手掌中心(plan.md §4.3)
        duration: float = 0.3,  # 已废弃(2026-09-18):set_target 流式驱动不再使用,
        #                        保留参数仅为 API 兼容
        smooth_alpha: float = 0.35,   # u/v EMA 平滑系数(越小越稳,越大越跟手)
        deadband_px: int = 10,        # 死区:平滑目标与上次发送差 < 该值不发送(防抖)
        gaze_gain_yaw_deg: float = 25.0,   # 归一化偏移 x∈[-1,1] → yaw 增益(度)
        gaze_gain_pitch_deg: float = 18.0, # 归一化偏移 y∈[-1,1] → pitch 增益(度)
        yaw_limit_deg: float = 35.0,       # 偏航安全限位
        pitch_limit_deg: float = 25.0,     # 俯仰安全限位
        flip_horizontal: bool = False,     # 相机硬件镜像时开:检测前水平翻转图像
        detect_width: int = 640,           # 检测前降采样宽度(4K 全解太慢会卡)
    ) -> None:
        self.orchestrator = orchestrator
        self.get_frame_jpeg_fn = get_frame_jpeg_fn
        self.poll_hz = max(poll_hz, 1.0)
        self.model_path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
        self.landmark_index = landmark_index
        self.duration = duration
        self.smooth_alpha = min(max(smooth_alpha, 0.05), 1.0)
        self.deadband_px = max(int(deadband_px), 0)
        self.gaze_gain_yaw_deg = float(gaze_gain_yaw_deg)
        self.gaze_gain_pitch_deg = float(gaze_gain_pitch_deg)
        self.yaw_limit_deg = abs(float(yaw_limit_deg))
        self.pitch_limit_deg = abs(float(pitch_limit_deg))
        self.flip_horizontal = bool(flip_horizontal)
        self.detect_width = max(int(detect_width), 160)

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled_lock = threading.Lock()
        self._enabled = False

        # 平滑状态(EMA)与防抖状态
        self._smooth_u: float | None = None
        self._smooth_v: float | None = None
        self._last_sent: tuple[int, int] | None = None
        # 无手时的画面亮度诊断(4K 帧 arr.mean(),仅在手消失时算一次)
        self._last_brightness: float | None = None

        self._landmarker: Any | None = None
        self._available: bool = False

        from reachymini_conversation.state_bus import get_state_bus

        self._bus = get_state_bus()
        self._bus.update("hand_follow_enabled", False)
        self._bus.update("hand_visible", False)
        self._bus.update("hand_uv", None)
        self._bus.update("hand_available", False)

    @property
    def available(self) -> bool:
        """HandLandmarker 是否可用(模型 + mediapipe 都 OK)。"""
        return self._available

    @property
    def enabled(self) -> bool:
        with self._enabled_lock:
            return self._enabled

    def enable(self) -> None:
        """开启跟随。"""
        with self._enabled_lock:
            self._enabled = True
        self._bus.update("hand_follow_enabled", True)
        logger.info("[HandFollower] enabled")

    def disable(self) -> None:
        """关闭跟随(头部不再动)。"""
        with self._enabled_lock:
            self._enabled = False
        self._bus.update("hand_follow_enabled", False)
        self._bus.update("hand_visible", False)
        # 清平滑/防抖状态:下次开启从当前检测值重新开始,不带历史惯性
        self._smooth_u = None
        self._smooth_v = None
        self._last_sent = None
        logger.info("[HandFollower] disabled")

    def start(self) -> None:
        """启动后台线程(懒加载 HandLandmarker)。"""
        if self._thread is not None and self._thread.is_alive():
            logger.debug("[HandFollower] already running")
            return

        # 懒加载模型前先探活 MediaPipe 原生库(SIGILL 免疫,见
        # _mediapipe_usable 注释;探测失败优雅降级,绝不拖死 app)
        if not self.model_path.exists():
            logger.warning(f"[HandFollower] 模型文件不存在:{self.model_path}")
            self._available = False
            self._bus.update("hand_available", False)
            logger.warning(
                "[HandFollower] disabled(模型不可用)。下载:"
                "https://storage.googleapis.com/mediapipe-models/"
                "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
            )
            return
        if not _mediapipe_usable(self.model_path):
            self._available = False
            self._bus.update("hand_available", False)
            logger.warning(
                "[HandFollower] disabled(本机 MediaPipe 原生库不可用,"
                "如 CM4 缺 AES 指令)——手部跟随关闭,其余功能不受影响"
            )
            return

        # 懒加载模型
        try:
            self._landmarker = self._load_landmarker()
            self._available = self._landmarker is not None
        except Exception as e:
            logger.warning(f"[HandFollower] 模型加载失败: {e}")
            self._available = False

        self._bus.update("hand_available", self._available)
        if not self._available:
            logger.warning(
                f"[HandFollower] disabled(模型不可用:{self.model_path})。"
                "下载:https://storage.googleapis.com/mediapipe-models/"
                "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
            )
            return  # 后台线程不起,因为没东西可做

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="hand-follower",
        )
        self._thread.start()
        logger.info(f"[HandFollower] started @ {self.poll_hz} Hz")

    def stop(self, timeout: float = 2.0) -> None:
        """停止后台线程。"""
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if self._landmarker is not None:
            try:
                self._landmarker.close()
            except Exception:
                pass
        logger.info("[HandFollower] stopped")

    def _load_landmarker(self) -> Any:
        """加载 HandLandmarker(从 .task 文件)。失败返回 None。"""
        if not self.model_path.exists():
            logger.warning(f"[HandFollower] 模型文件不存在:{self.model_path}")
            return None

        try:
            import mediapipe as mp

            BaseOptions = mp.tasks.BaseOptions
            HandLandmarker = mp.tasks.vision.HandLandmarker
            HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
            VisionTaskRunningMode = mp.tasks.vision.RunningMode

            options = HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(self.model_path)),
                running_mode=VisionTaskRunningMode.VIDEO,
                num_hands=1,
                min_hand_detection_confidence=0.5,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            return HandLandmarker.create_from_options(options)
        except Exception as e:
            logger.error(f"[HandFollower] HandLandmarker 加载失败: {e}")
            return None

    def _loop(self) -> None:

        interval = 1.0 / self.poll_hz
        logger.info(f"[hand-follower] loop started, interval={interval:.3f}s")
        last_requested: bool | None = None
        no_hand_since: float | None = None
        last_no_hand_log = 0.0
        while not self._stop.is_set():
            try:
                # 同步 state_bus.hand_follow_requested → enable/disable
                requested = self._bus.get("hand_follow_requested")
                if requested is not None and requested != last_requested:
                    if requested:
                        self.enable()
                    else:
                        self.disable()
                    last_requested = requested

                had_hand = bool(self._bus.get("hand_visible"))
                self._tick()

                # 真机诊断:已开启但持续无手 → 每 5s 提示一次排查方向
                # (2026-09-17 用户实测"一直检测不到手":根因多为取帧源不对/
                #  未连真机/手不在镜头前,日志里要能自解释)
                if self.enabled:
                    if not had_hand:
                        now = time.monotonic()
                        if no_hand_since is None:
                            no_hand_since = now
                        elif now - no_hand_since >= 5.0 and now - last_no_hand_log >= 5.0:
                            last_no_hand_log = now
                            dark_hint = ""
                            b = self._last_brightness
                            if b is not None and b < 45.0:
                                dark_hint = (
                                    f";画面偏暗(亮度 {b:.0f}/255)——补光或检查镜头遮挡"
                                )
                            logger.info(
                                "[hand-follower] 已开启但持续未检测到手 — "
                                "确认:①顶栏已⚡连接真机(纯仿真/无线模式的画面"
                                "里没有真手)②手在机器人镜头前 ③光线充足"
                                f"{dark_hint}"
                            )
                    else:
                        no_hand_since = None
                else:
                    no_hand_since = None
            except Exception as e:
                logger.warning(f"[hand-follower] tick error: {e}")
            if self._stop.wait(interval):
                break
        logger.info("[hand-follower] loop exited")

    def _tick(self) -> None:
        """一次轮询:取一帧 + 检测手 + look_at_image。"""
        # 1. 是否启用(本地 enabled 或 state_bus.requested)
        if not self.enabled:
            requested = self._bus.get("hand_follow_requested")
            if not requested:
                return
        # 2. 取帧
        if self.get_frame_jpeg_fn is None or self._landmarker is None:
            return
        try:
            jpeg_bytes = self.get_frame_jpeg_fn()
        except Exception as e:
            logger.debug(f"[hand-follower] get_frame_jpeg failed: {e}")
            return
        if not jpeg_bytes:
            return

        # 3. JPEG bytes → numpy RGB array(检测前降采样:4K 全尺寸 PIL 解码
        #    实测 69ms/帧,tick 追不上 15Hz → 跟随卡顿。PIL draft 对 MJPEG
        #    流实测不生效(尺寸不变);cv2.imdecode 走 IDCT 层 1/4 降采样
        #    解码仅 ~11ms(960x540,精度足够:MediaPipe 内部跑 224px ROI)。
        #    cv2 由 mediapipe 传递依赖保证存在,失败回退 PIL 路径)
        try:
            import numpy as np

            arr = None
            try:
                import cv2

                bgr = cv2.imdecode(
                    np.frombuffer(jpeg_bytes, dtype=np.uint8),
                    cv2.IMREAD_REDUCED_COLOR_4,  # 1/4 IDCT 降采样
                )
                if bgr is not None:
                    arr = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            except Exception as e:
                logger.debug(f"[hand-follower] cv2 解码失败,回退 PIL: {e}")
                arr = None
            if arr is None:
                from PIL import Image as PILImage

                img = PILImage.open(__import__("io").BytesIO(jpeg_bytes)).convert("RGB")
                _w = getattr(img, "width", None)
                if _w is not None and _w > self.detect_width and hasattr(img, "resize"):
                    img = img.resize(
                        (self.detect_width, int(img.height * self.detect_width / img.width))
                    )
                arr = np.array(img)
            if arr.shape[1] > self.detect_width:
                import cv2

                arr = cv2.resize(
                    arr,
                    (self.detect_width, int(arr.shape[0] * self.detect_width / arr.shape[1])),
                    interpolation=cv2.INTER_AREA,
                )
            if self.flip_horizontal:
                # 相机硬件镜像修正(检测坐标系翻转);fliplr 产生负步长视图,
                # 转连续内存避免下游(np/mediapipe)兼容问题
                arr = np.ascontiguousarray(np.fliplr(arr))
            h, w = arr.shape[:2]
        except Exception as e:
            logger.debug(f"[hand-follower] JPEG decode failed: {e}")
            return

        # 4. MediaPipe 检测(VIDEO 模式需要 timestamp_ms)
        try:
            import mediapipe as mp

            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=arr)
            # 单调递增时间戳(VIDEO 模式要求)
            timestamp_ms = int(self._bus.get("hand_tick_ms", 0)) + int(1000 / self.poll_hz)
            self._bus.update("hand_tick_ms", timestamp_ms)
            result = self._landmarker.detect_for_video(mp_image, timestamp_ms)
        except Exception as e:
            logger.debug(f"[hand-follower] detect failed: {e}")
            return

        # 5. 解析结果
        if not result.hand_landmarks:
            self._bus.update("hand_visible", False)
            # 亮度诊断:检测不到手时记录画面亮度(供"开启但无手"日志提示补光)
            try:
                self._last_brightness = float(arr.mean())
            except Exception:
                pass
            return

        landmarks = result.hand_landmarks[0]  # 取第一只手
        if self.landmark_index >= len(landmarks):
            return
        palm = landmarks[self.landmark_index]
        u = int(palm.x * w)
        v = int(palm.y * h)

        self._bus.update("hand_visible", True)
        self._bus.update("hand_uv", [u, v])

        # 5b. 播报让位:Reachy 说话/播放时头部归 wobbler(特别动作),
        #     检测照常更新 bus(徽章仍实时),但不驱动头部
        if self._bus.get("status") in self._YIELD_STATES:
            return

        # 5c. EMA 平滑 + 死区防抖(15Hz 原始检测直接发会抖)
        if self._smooth_u is None:
            self._smooth_u, self._smooth_v = float(u), float(v)
        else:
            a = self.smooth_alpha
            self._smooth_u = a * u + (1.0 - a) * self._smooth_u
            self._smooth_v = a * v + (1.0 - a) * self._smooth_v
        su, sv = int(self._smooth_u), int(self._smooth_v)
        if self._last_sent is not None:
            du = abs(su - self._last_sent[0])
            dv = abs(sv - self._last_sent[1])
            if du < self.deadband_px and dv < self.deadband_px:
                return  # 死区内:不发送,减少关节指令抖动
        self._last_sent = (su, sv)

        # 6. 驱动头部:像素偏移 → yaw/pitch 线性映射 → set_target(sim+real 镜像)
        #    set_target 为 fire-and-forget(15Hz 流式小步进,daemon 伺服追踪),
        #    绝不能用 goto_target —— 它是同步阻塞调用,镜像双实例会把事件
        #    循环冻住、有效更新率掉到 ~1.6Hz(卡顿根因,见模块 docstring)。
        #    符号约定(SDK 源码推导,见模块 docstring):
        #      正 pitch=低头、正 yaw=转向图像左
        #      → yaw=-nx*gain(手在图像右→看右),pitch=+ny*gain(手在图像下→看下)
        if self.orchestrator is None:
            return
        nx = (su - w * 0.5) / (w * 0.5)   # -1..1(图像中心为 0,右正)
        ny = (sv - h * 0.5) / (h * 0.5)   # -1..1(v 向下为正)
        yaw_deg = max(
            -self.yaw_limit_deg,
            min(self.yaw_limit_deg, -nx * self.gaze_gain_yaw_deg),
        )
        pitch_deg = max(
            -self.pitch_limit_deg,
            min(self.pitch_limit_deg, ny * self.gaze_gain_pitch_deg),
        )
        try:
            from reachy_mini.utils import create_head_pose

            head_pose = create_head_pose(
                x=0, y=0, z=0, roll=0, pitch=pitch_deg, yaw=yaw_deg, degrees=True
            )
        except Exception as e:
            logger.debug(f"[hand-follower] 构建头部姿态失败: {e}")
            return

        try:
            import asyncio

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self.orchestrator.set_target(head=head_pose),
                        loop,
                    )
                else:
                    asyncio.run(self.orchestrator.set_target(head=head_pose))
            except RuntimeError:
                asyncio.run(self.orchestrator.set_target(head=head_pose))
        except Exception as e:
            logger.debug(f"[hand-follower] set_target failed: {e}")
