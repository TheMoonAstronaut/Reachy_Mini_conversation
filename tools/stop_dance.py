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
        # 2026-09-18:dance 已后台线程化(_BG_PERFORM_TOOLS)。SDK move 不
        # 支持中途取消,stop 语义 = "不再起下一个"(同原始设计);这里仅
        # 汇报后台线程是否仍在跑,便于 LLM 组织回复。
        from tools.core_tools import is_tool_running

        return {"status": "stop signal sent", "dance_running": is_tool_running("dance")}
