"""reachymini_conversation.idle_breath — 空闲待机"呼吸"微动(官方移植)。

源(source-driven,2026-09-16 调研):
  GitHub pollen-robotics/reachy_mini_conversation_app
  - src/reachy_mini_conversation_app/moves.py 的 BreathingMove:
    1s 插值进 neutral → 呼吸 z ±5mm @0.1Hz(6 次/分)+ 天线 ±15° @0.5Hz
    反向摆动;neutral antennas (±10°) 防舵机抖动;duration=inf 无限循环。
  - 调度:moves.py 主循环在 last_activity 超过 idle_inactivity_delay 后启动
    BreathingMove,新 move 入队即打断。

移植差异(我们的架构无 60Hz 控制循环):
  - duration 改有限段(8s/循环),每段重新从当前位姿插值进入 → 视觉连续;
  - IdleBreathController 后台线程 2s 粒度轮询 state_bus 的活动字段
    (last_reply / voice_turn_seq / last_tool_calls),空闲超时播一段;
  - 打断延迟 ≤ 一段时长(8s):段播完发现已有活动就不再续播。
    (官方在 60Hz 循环里即时打断;我们接受段粒度延迟,视觉上是呼吸自然收尾)

防冲突(2026-09-16 用户定语义):**只有 Reachy 自己说话(speaking/playing)
时不播呼吸** —— 那时由音频 wobbler 驱动头部摆动作"特别动作";
用户说话(listening)、语义分析(thinking)、空闲待机都播呼吸当底色动作。
播放期间若用户发命令,当前段播完即停(工具的 set_target 立即接管)。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

import numpy as np
from numpy.typing import NDArray
from reachy_mini.motion.move import Move
from reachy_mini.utils import create_head_pose
from reachy_mini.utils.interpolation import linear_pose_interpolation

logger = logging.getLogger(__name__)

# ---- 官方 BreathingMove 参数(moves.py:79-83,照抄)----
BREATH_Z_AMPLITUDE_M = 0.005     # 5mm 呼吸起伏
BREATH_FREQ_HZ = 0.1             # 6 次/分钟
ANTENNA_SWAY_RAD = np.deg2rad(15)  # ±15° 天线摆动
ANTENNA_FREQ_HZ = 0.5
NEUTRAL_ANTENNAS = (-0.1745, 0.1745)  # ±10° 防舵机抖动
INTERP_DURATION_S = 1.0          # 进入呼吸前的插值时长
SEGMENT_CYCLES_S = 8.0           # 每段时长(一个呼吸循环)

# 活动检测:这些 bus 字段变化 = 有活动
_ACTIVITY_KEYS = ("last_reply", "voice_turn_seq", "last_tool_calls", "chat_mode", "run_mode")

# 忙碌状态(bus.status):只有"speaking/playing"(Reachy 自己播报)时不播 ——
# 播报由音频 wobbler 驱动头部摆动(特别动作),不与呼吸叠加;
# listening(用户说话)/thinking(语义分析)/空闲都播呼吸。
# 注意用否定集合而非白名单:bus 初始 status="starting"(无对话时一直是它),
# 白名单写法会让空闲呼吸永不启动(2026-09-16 测试暴露)。
_BUSY_STATES = frozenset({"speaking", "playing"})


class BreathingMove(Move):
    """官方呼吸待机动作(参数照抄,见模块 docstring 源引用)。

    与官方差异:duration 有限(segment_s × cycles),段与段之间重新插值。
    """

    def __init__(
        self,
        interpolation_start_pose: NDArray[np.float64] | NDArray[np.float32],
        interpolation_start_antennas: tuple[float, float],
        cycles: float = 1.0,
    ) -> None:
        self.interpolation_start_pose = np.asarray(interpolation_start_pose)
        self.interpolation_start_antennas = np.array(interpolation_start_antennas)
        self.interpolation_duration = INTERP_DURATION_S
        self._cycles = max(0.5, float(cycles))

        self.neutral_head_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        self.neutral_antennas = np.array(NEUTRAL_ANTENNAS)

    @property
    def duration(self) -> float:
        return self.interpolation_duration + SEGMENT_CYCLES_S * self._cycles

    def evaluate(self, t: float):
        if t < self.interpolation_duration:
            k = t / self.interpolation_duration
            head_pose = linear_pose_interpolation(
                self.interpolation_start_pose, self.neutral_head_pose, k
            )
            antennas = (
                (1 - k) * self.interpolation_start_antennas + k * self.neutral_antennas
            ).astype(np.float64)
            return head_pose, antennas, 0.0

        bt = t - self.interpolation_duration
        z_offset = BREATH_Z_AMPLITUDE_M * float(np.sin(2 * np.pi * BREATH_FREQ_HZ * bt))
        head_pose = create_head_pose(
            x=0, y=0, z=z_offset, roll=0, pitch=0, yaw=0, degrees=True, mm=False
        )
        sway = ANTENNA_SWAY_RAD * float(np.sin(2 * np.pi * ANTENNA_FREQ_HZ * bt))
        antennas = np.array([sway, -sway], dtype=np.float64)
        return head_pose, antennas, 0.0


class IdleBreathController:
    """空闲检测 + 呼吸播放调度(后台线程)。

    Args:
        target: 动作目标(MirroredToolTarget 或任何有 async_play_move 的对象)
        idle_after_s: 无活动多少秒后开始呼吸(默认 10s;对话间隙也连贯)
        check_interval_s: 轮询间隔(默认 2s)
        pose_getter: 返回 (head_pose_4x4, antennas(2,)) 的 callable,
            用于插值起点(默认 None → 从 neutral 开始,不插)
    """

    def __init__(
        self,
        target: Any,
        *,
        idle_after_s: float = 10.0,
        check_interval_s: float = 2.0,
        pose_getter: Any = None,
        post_speech_grace_s: float = 2.0,
    ) -> None:
        self._target = target
        self._idle_after_s = idle_after_s
        self._check_interval_s = check_interval_s
        self._pose_getter = pose_getter
        self._post_speech_grace_s = post_speech_grace_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._playing = False
        self._last_activity = time.monotonic()
        self._last_seen: dict[str, Any] = {}
        self._status: str = "idle"  # 上一轮 status(下降沿检测用)

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="idle-breath", daemon=True
        )
        self._thread.start()
        logger.info(
            f"[idle-breath] 已启动(空闲 {self._idle_after_s}s 后进入呼吸待机)"
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        logger.info("[idle-breath] 已停止")

    # ---------- 内部 ----------
    def _observe_activity(self) -> None:
        """轮询 bus 活动字段,变化即刷新 last_activity。"""
        from reachymini_conversation.state_bus import get_state_bus

        snap = get_state_bus().snapshot()
        for k in _ACTIVITY_KEYS:
            v = snap.get(k)
            if k not in self._last_seen:
                self._last_seen[k] = v
                continue
            if v != self._last_seen[k] and v is not None:
                self._last_seen[k] = v
                self._last_activity = time.monotonic()

        prev_status = self._status
        self._status = snap.get("status", "idle")
        self._run_mode = snap.get("run_mode", "pure_sim")

        # 播报结束沿(speaking/playing → 非 busy):wobbler 摆动停止的瞬间
        # 就是新一段"机器人空闲"的开始 —— 把 idle 计时起点拨到播完时刻
        # (留 post_speech_grace_s 衔接宽限)。否则短回答播完后要等满
        # idle_after_s,中间出现一段"动作真空"(2026-09-16 用户实测:
        # "回答后的动作结束后,会有一段时间待机动作停止")。
        if prev_status in _BUSY_STATES and self._status not in _BUSY_STATES:
            self._last_activity = (
                time.monotonic() - self._idle_after_s + self._post_speech_grace_s
            )

    def _play_one_segment(self) -> None:
        """播一段 8s 呼吸(同步包装 async_play_move)。"""
        start_pose = None
        start_antennas = NEUTRAL_ANTENNAS
        if self._pose_getter is not None:
            try:
                start_pose, start_antennas = self._pose_getter()
            except Exception as e:
                logger.debug(f"[idle-breath] 取当前位姿失败,用 neutral 起点: {e}")
        if start_pose is None:
            start_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        move = BreathingMove(start_pose, tuple(start_antennas), cycles=1)
        self._playing = True
        try:
            asyncio.run(self._target.async_play_move(move))
        except Exception as e:
            logger.warning(f"[idle-breath] 播放异常: {type(e).__name__}: {e}")
        finally:
            self._playing = False

    def _run(self) -> None:
        from reachymini_conversation.state_bus import get_state_bus  # noqa: F401

        self._observe_activity()  # 初始化基线
        while not self._stop.is_set():
            time.sleep(self._check_interval_s)
            self._observe_activity()
            idle_for = time.monotonic() - self._last_activity
            if (
                idle_for >= self._idle_after_s
                and not self._playing
                and getattr(self, "_status", "idle") not in _BUSY_STATES
            ):
                logger.info(f"[idle-breath] 空闲 {idle_for:.0f}s → 呼吸一段")
                try:
                    self._play_one_segment()
                except Exception as e:
                    logger.warning(f"[idle-breath] 播放失败: {e}")
                    time.sleep(2.0)  # 失败退避,防 tight loop
