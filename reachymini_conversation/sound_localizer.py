"""reachymini_conversation.sound_localizer — 声源定位(决策 4,P5)。

基于 SDK `AudioDoA`:
  - 后台线程 10 Hz 轮询
  - VAD 检测到语音 → 头部/身体转向声源
  - 静默 3 秒 → 回中
  - 当前角度 / 语音状态写入 StateBus(给 UI 显示 + look_at_sound 工具用)

DoA 角度约定(SDK 文档):
  - 0 rad = 左
  - π/2 rad = 前(或后,180° ambiguous)
  - π rad = 右

到 Reachy body_yaw 的映射(plan.md §4.2):
  - body_yaw_deg = degrees(angle_rad) - 90
  - 即 DoA 0(left)→ body_yaw -90(转向左)
        DoA π/2(front)→ body_yaw 0(正视)
        DoA π(right)→ body_yaw +90(转向右)

降级:
  - 无 ReSpeaker 硬件 → available=False → 后台线程空转,UI 显 N/A
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class SoundLocalizer:
    """声源定位器:后台读 AudioDoA,触发身体转向。

    用法:
        sl = SoundLocalizer(orchestrator=mirror_orch, doa_hz=10.0, return_to_center_sec=3.0)
        sl.start()  # 后台线程
        # ... 后续 sl.stop() 停止
    """

    def __init__(
        self,
        orchestrator: Any,
        doa_hz: float = 10.0,
        return_to_center_sec: float = 3.0,
    ) -> None:
        self.orchestrator = orchestrator
        self.doa_hz = max(doa_hz, 0.5)  # 最低 0.5 Hz
        self.return_to_center_sec = max(return_to_center_sec, 0.5)

        # 懒加载 AudioDoA(避免 import 时无硬件就崩)
        self._doa: Any | None = None
        self._available: bool = False
        # V2 修复:声源跟随默认**关**(用户反馈:真机接入后 ReSpeaker 活了,
        # 环境音莫名触发转头)。读 DoA 更新徽章照常,只是不驱动身体;
        # UI「🧭 声源跟随」开关显式开启(写 bus.doa_follow_enabled)。
        self._enabled: bool = False

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._last_speech_time: float = 0.0
        self._last_angle_deg: float | None = None

        # 给 state_bus 写
        from reachymini_conversation.state_bus import get_state_bus

        self._bus = get_state_bus()
        self._bus.update("doa_angle", None)
        self._bus.update("doa_speech", False)
        self._bus.update("doa_available", False)
        self._bus.update("doa_follow_enabled", False)

    @property
    def available(self) -> bool:
        """AudioDoA 是否可用(有 ReSpeaker 硬件)。"""
        return self._available

    # ---------- V2:跟随开关(默认关)----------
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """启停声源跟随(UI 开关调用)。开启时也回中一次,避免残留角度。"""
        self._enabled = bool(enabled)
        self._bus.update("doa_follow_enabled", self._enabled)
        if self._enabled:
            self._last_speech_time = 0.0  # 重新计时,防止立刻回中
        logger.info(f"[SoundLocalizer] 声源跟随 {'开启' if self._enabled else '关闭'}")

    def start(self) -> None:
        """启动后台线程。重复调用安全(no-op)。"""
        if self._thread is not None and self._thread.is_alive():
            logger.debug("[SoundLocalizer] already running")
            return

        # 懒加载 AudioDoA
        try:
            from reachy_mini.media.audio_doa import AudioDoA

            self._doa = AudioDoA()
            self._available = self._doa.available
        except Exception as e:
            logger.warning(f"[SoundLocalizer] AudioDoA 初始化失败: {e}")
            self._available = False

        self._bus.update("doa_available", self._available)
        logger.info(
            f"[SoundLocalizer] starting @ {self.doa_hz} Hz, "
            f"hardware={'available' if self._available else 'NOT available (degraded)'}"
        )

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="sound-localizer",
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """停止后台线程。"""
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if self._doa is not None:
            try:
                self._doa.close()
            except Exception:
                pass
        logger.info("[SoundLocalizer] stopped")

    def current_angle(self) -> float | None:
        """最近一次 DoA 角度(度)。None 表示还没有读。"""
        return self._last_angle_deg

    # ---------- 后台主循环 ----------
    def _loop(self) -> None:
        interval = 1.0 / self.doa_hz
        logger.info(f"[sound-localizer] loop started, interval={interval:.3f}s")

        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.warning(f"[sound-localizer] tick error: {e}")
                # 不死,继续轮询
            if self._stop.wait(interval):
                break
        logger.info("[sound-localizer] loop exited")

    def _tick(self) -> None:
        """一次轮询。"""
        # 1. 读 DoA(硬件缺失时返回 None)
        if self._doa is None or not self._available:
            self._bus.update("doa_speech", False)
            return

        result = self._doa.get_DoA()
        if result is None:
            self._bus.update("doa_speech", False)
            return

        angle_rad, speech = result
        angle_deg = math.degrees(angle_rad) - 90.0  # 映射到 body_yaw

        # 写 state bus
        self._last_angle_deg = angle_deg
        self._bus.update("doa_angle", angle_deg)
        self._bus.update("doa_speech", speech)
        self._bus.update("doa_angle_rad", float(angle_rad))

        # 2. 决策:语音 → 转向声源;静默 → 回中
        #    V2:仅当开关开启才驱动身体(badge 角度显示不受开关影响)
        if not self._enabled:
            return
        if speech:
            self._last_speech_time = time.monotonic()
            self._apply_yaw(angle_deg)
        elif time.monotonic() - self._last_speech_time > self.return_to_center_sec:
            # 回中(只在没语音时,且超时)
            self._apply_yaw(0.0)

    def _apply_yaw(self, target_yaw_deg: float) -> None:
        """调 MirrorOrchestrator.set_target(body_yaw=...)。"""
        if self.orchestrator is None:
            return
        try:
            # P2 MirrorOrchestrator 提供 set_target(head=None, body_yaw=...)
            import asyncio

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # 在跑 loop(主 Gradio event loop),用 run_coroutine_threadsafe
                    asyncio.run_coroutine_threadsafe(
                        self.orchestrator.set_target(body_yaw=target_yaw_deg),
                        loop,
                    )
                    # 不 wait(避免阻塞线程),fire-and-forget
                else:
                    asyncio.run(self.orchestrator.set_target(body_yaw=target_yaw_deg))
            except RuntimeError:
                # 没 loop
                asyncio.run(self.orchestrator.set_target(body_yaw=target_yaw_deg))
        except Exception as e:
            logger.debug(f"[sound-localizer] apply_yaw({target_yaw_deg}) failed: {e}")
