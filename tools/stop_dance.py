"""tools.stop_dance — 停止舞蹈工具。

P0.4 简化:SDK play_move 不需要显式停止,只要不再调用下一个 move,机器人就会自然回中。
本工具的语义变成"标记意图停止" + 清空 MovementManager shim 的(空)队列。
"""
from __future__ import annotations

import logging
from typing import Any

from tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)


class StopDance(Tool):
    """停止当前舞蹈动作。"""

    name = "stop_dance"
    description = "Stop the current dance move (no-op if none is running)."
    parameters_schema = {
        "type": "object",
        "properties": {
            "dummy": {
                "type": "boolean",
                "description": "dummy boolean, set it to true",
            },
        },
        "required": ["dummy"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        logger.info("Tool call: stop_dance")
        # P0.4:clear_move_queue 是 shim no-op;SDK play_move 不需要显式停止
        if deps.movement_manager is not None:
            deps.movement_manager.clear_move_queue()
        return {"status": "stop signal sent"}
