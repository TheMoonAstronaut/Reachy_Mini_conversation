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

模型下载(用户首次使用):
  - https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task
  - 保存到 ~/.cache/reachymini/hand_landmarker.task(或环境变量指定)
  - P6 不自动下载,留给用户
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# 默认模型路径(用户首次跑会下载)
DEFAULT_MODEL_PATH = Path.home() / ".cache" / "reachymini" / "hand_landmarker.task"


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
        duration: float = 0.3,
        smooth_alpha: float = 0.35,   # u/v EMA 平滑系数(越小越稳,越大越跟手)
        deadband_px: int = 10,        # 死区:平滑目标与上次发送差 < 该值不发送(防抖)
    ) -> None:
        self.orchestrator = orchestrator
        self.get_frame_jpeg_fn = get_frame_jpeg_fn
        self.poll_hz = max(poll_hz, 1.0)
        self.model_path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
        self.landmark_index = landmark_index
        self.duration = duration
        self.smooth_alpha = min(max(smooth_alpha, 0.05), 1.0)
        self.deadband_px = max(int(deadband_px), 0)

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled_lock = threading.Lock()
        self._enabled = False

        # 平滑状态(EMA)与防抖状态
        self._smooth_u: float | None = None
        self._smooth_v: float | None = None
        self._last_sent: tuple[int, int] | None = None

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

                self._tick()
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

        # 3. JPEG bytes → numpy RGB array
        try:
            import numpy as np
            from PIL import Image as PILImage

            img = PILImage.open(__import__("io").BytesIO(jpeg_bytes)).convert("RGB")
            arr = np.array(img)  # (H, W, 3) RGB uint8
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

        # 6. look_at_image(sim + real)
        if self.orchestrator is None:
            return
        try:
            import asyncio

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self.orchestrator.look_at_image(su, sv, self.duration),
                        loop,
                    )
                else:
                    asyncio.run(self.orchestrator.look_at_image(su, sv, self.duration))
            except RuntimeError:
                asyncio.run(self.orchestrator.look_at_image(su, sv, self.duration))
        except Exception as e:
            logger.debug(f"[hand-follower] look_at_image failed: {e}")
