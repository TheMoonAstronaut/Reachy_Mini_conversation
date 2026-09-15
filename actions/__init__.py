"""Actions package for Reachy Mini motion control.

P0.4 决策:删除 `move_queue.py`(重复实现),改用 SDK play_move/goto_target/set_target。
`MovementManager` 保留为兼容 shim,见 `actions/movement.py`。
"""
from .movement import MovementManager
from .poses import NEUTRAL_POSE, SLEEP_POSE

__all__ = ["MovementManager", "NEUTRAL_POSE", "SLEEP_POSE"]