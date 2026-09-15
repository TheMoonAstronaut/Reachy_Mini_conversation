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

    def __init__(
        self,
        orchestrator: Any,
        get_frame_jpeg_fn: Any | None = None,
        poll_hz: float = 15.0,
        model_path: str | None = None,
        landmark_index: int = 9,  # 中指 MCP = 手掌中心(plan.md §4.3)
        duration: float = 0.3,
    ) -> None:
        self.orchestrator = orchestrator
        self.get_frame_jpeg_fn = get_frame_jpeg_fn
        self.poll_hz = max(poll_hz, 1.0)
        self.model_path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
        self.landmark_index = landmark_index
        self.duration = duration

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled_lock = threading.Lock()
        self._enabled = False

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

        # 6. look_at_image(sim + real)
        if self.orchestrator is None:
            return
        try:
            import asyncio

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self.orchestrator.look_at_image(u, v, self.duration),
                        loop,
                    )
                else:
                    asyncio.run(self.orchestrator.look_at_image(u, v, self.duration))
            except RuntimeError:
                asyncio.run(self.orchestrator.look_at_image(u, v, self.duration))
        except Exception as e:
            logger.debug(f"[hand-follower] look_at_image failed: {e}")
