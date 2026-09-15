"""actions.movement — 简化的运动协调层。

P0.4 决策:移除 `move_queue.py` 重复实现的 MovementManager,
改用 SDK 原生 `play_move()` / `goto_target()` / `set_target()`。
本模块保留 `MovementManager` 类名作为兼容 shim(只占接口不做事),
P4 之后可彻底删除。

原实现(593 行)的功能:
- BreathingMove / DanceQueueMove / EmotionQueueMove / GotoQueueMove 4 个 Move 子类
- MovementManager 60Hz 控制循环 + move queue + breathing 自动续接 + 离线 offsets
- 后台线程管理

替代方案(SDK 提供):
- 单次动作 → `reachy_mini.play_move(DanceMove(name))`
- 一次性头部姿态 → `reachy_mini.goto_target(head=..., duration=...)`
- 持续音频反应式摆头 → `reachy_mini.enable_wobbling()`(已替代 HeadWobbler)
- 多动作串行 → 由调用方按顺序 await play_move() 即可
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class MovementManager:
    """运动协调 shim(向后兼容,无实际功能)。

    P0.4 决策:SDK 已覆盖所有核心能力,本类只保留:
    - 构造器签名:`MovementManager(current_robot, camera_worker=None)`
    - 生命周期:`start()` / `stop()`(no-op)
    - 状态设置:`set_speech_offsets()`(no-op,SDK 内置)
    - 队列清理:`clear_move_queue()`(no-op,改由 SDK play_move 控制)

    P4 之后:本类可彻底删除,所有调用方改为直接 await SDK 方法。
    """

    def __init__(self, current_robot: Any, camera_worker: Any | None = None) -> None:
        self.current_robot = current_robot
        self.camera_worker = camera_worker
        logger.debug("MovementManager shim initialized (no-op)")

    # ---------- 生命周期(no-op)----------
    def start(self) -> None:
        """启动(无操作,SDK play_move 是即时的)。"""
        logger.debug("MovementManager.start() — no-op in P0.4 shim")

    def stop(self) -> None:
        """停止(无操作)。"""
        logger.debug("MovementManager.stop() — no-op in P0.4 shim")

    # ---------- 状态接口(向后兼容,无操作)----------
    def set_speech_offsets(self, offsets: tuple[float, ...] = ()) -> None:
        """设置语音偏移(SDK `enable_wobbling()` 已内置,这里 no-op)。"""
        logger.debug("MovementManager.set_speech_offsets() — no-op in P0.4 shim")

    def clear_move_queue(self) -> None:
        """清空队列(SDK play_move 不需要队列)。"""
        logger.debug("MovementManager.clear_move_queue() — no-op in P0.4 shim")

    def queue_move(self, move: Any) -> None:
        """加入队列(已废弃,改用 SDK play_move 直接调用)。"""
        logger.debug("MovementManager.queue_move() — no-op in P0.4 shim")
