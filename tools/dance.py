"""tools.dance — 舞蹈工具(P0.4 简化:直接用 SDK DanceMove + play_move)。"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from reachy_mini_dances_library.collection.dance import AVAILABLE_MOVES
from reachy_mini_dances_library.dance_move import DanceMove

from tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)

DANCE_AVAILABLE = True  # 决策 7 决策保留 dance 库


def get_available_dances_and_descriptions() -> str:
    """列出所有可用舞蹈 + 描述(供 LLM 工具 schema 引用)。"""
    if not AVAILABLE_MOVES:
        return "Moves not available."
    output = ""
    for move_name, (_func, _params, metadata) in AVAILABLE_MOVES.items():
        description = metadata.get("description", "No description available.")
        output += f"{move_name}: {description}\n"
    return output


class Dance(Tool):
    """让机器人跳一段舞(SDK DanceMove + play_move)。"""

    name = "dance"
    # V1 修复:LLM 倾向显式传 move='simple_nod'(名字最安全)→ 用户看到"只点头"。
    # 描述改为强引导:默认留空随机;只有用户明确点名舞种时才传 move。
    description = (
        "Play a dance: the robot sways its body, bops its head and wiggles its "
        "antennas to the rhythm. By default this picks a RANDOM dance move — "
        "call it WITHOUT the 'move' argument for a random dance."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "move": {
                "type": "string",
                "enum": list(AVAILABLE_MOVES.keys()),
                "description": (
                    "ONLY set this when the user explicitly names a specific dance "
                    "style; otherwise OMIT it to get a random dance. "
                    "Available moves: \n"
                    f"{get_available_dances_and_descriptions()}"
                ),
            },
            "repeat": {
                "type": "integer",
                "description": "How many times to repeat the move (default 1).",
            },
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        move_name = kwargs.get("move")
        repeat = max(1, int(kwargs.get("repeat", 1)))

        if not AVAILABLE_MOVES:
            return {"error": "No moves currently available"}

        if not move_name:
            move_name = random.choice(list(AVAILABLE_MOVES.keys()))

        if move_name not in AVAILABLE_MOVES:
            return {
                "error": f"Unknown dance move '{move_name}'. Available: {list(AVAILABLE_MOVES.keys())}"
            }

        logger.info("Tool call: dance move=%s repeat=%d", move_name, repeat)

        # P7.B fix: 用 async_play_move (native async) 代替 sync play_move。
        # sync play_move 内部用 AsyncToSync wrapper,在已有 async event loop 的
        # 线程里抛 RuntimeError(同 play_emotion.py:99 bug)。
        for _ in range(repeat):
            dance_move = DanceMove(move_name)
            await deps.reachy_mini.async_play_move(dance_move)
            # 给机器人一点时间启动下一个 move,避免堵塞太快
            if repeat > 1:
                await asyncio.sleep(0.1)

        return {"status": "playing", "move": move_name, "repeat": repeat}
