"""state_bus — 状态总线(简化版,只用于 P1 头部 RPY)。

设计目标:
  - 一个进程内的状态容器,多个 producer / consumer 共享
  - 简单 get / set,线程安全(threading.Lock)
  - P1 只需要 polling(Gradio Timer 每秒读一次),不需要 push
  - 后续 P3+ 可以扩展为 asyncio.Queue / WebSocket 推送

API:
  - `update(key: str, value: Any)` — 写入
  - `get(key: str, default=None) -> Any` — 读单个
  - `snapshot() -> dict[str, Any]` — 读全部(给 Gradio JSON 用)

状态字段(P1):
  - `status` — str, "starting" / "running" / "stopping" / "stopped"
  - `head_pose` — list[float] | None, 当前头部 4x4 矩阵 flatten
  - `head_joints` — list[float] | None, 7 个头部关节(度)
  - `antennas` — list[float] | None, 2 个天线关节
  - `last_update` — float, time.monotonic()
  - `error` — str | None
"""

from __future__ import annotations

import threading
import time
from typing import Any


class StateBus:
    """进程内共享状态(P1 简化版)。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "status": "starting",
            "head_pose": None,
            "head_joints": None,
            "antennas": None,
            "last_update": None,
            "error": None,
        }

    # ---------- 写入 ----------
    def update(self, key: str, value: Any) -> None:
        """原子写入一个键(线程安全)。"""
        with self._lock:
            self._state[key] = value

    def update_many(self, values: dict[str, Any]) -> None:
        """批量写入(单次加锁)。"""
        with self._lock:
            self._state.update(values)
            self._state["last_update"] = time.monotonic()

    # ---------- 读取 ----------
    def get(self, key: str, default: Any = None) -> Any:
        """原子读单个键。"""
        with self._lock:
            return self._state.get(key, default)

    def snapshot(self) -> dict[str, Any]:
        """读所有状态(返回副本,避免外部修改)。"""
        with self._lock:
            return dict(self._state)


# ============================================================================
# 模块级单例(全进程共享一个状态)
# ============================================================================
_global_bus: StateBus | None = None


def get_state_bus() -> StateBus:
    """获取(或懒创建)全局 StateBus 单例。"""
    global _global_bus
    if _global_bus is None:
        _global_bus = StateBus()
    return _global_bus


def reset_state_bus() -> None:
    """重置全局 StateBus(测试 / 重启用)。"""
    global _global_bus
    _global_bus = None
