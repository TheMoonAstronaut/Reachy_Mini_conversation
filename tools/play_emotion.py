"""tools.play_emotion — 情感动作工具(决策 16D,P7 实装)。

行为:
  1. 尝试 import `reachy_mini_emotions_library`(HF 数据集,需单独安装)
  2. 有 → 调它的 play_emotion(emotion_name)
  3. 没 → fallback 到 `DanceMove`(从 dances_library),按名称映射

emotion 名称(决策 16D examples):
  - "happy" → DanceMove("groovy_sway_and_roll")
  - "sad"   → DanceMove("side_to_side_sway")
  - "thinking" → DanceMove("chin_lead")
  - "greeting" → DanceMove("neck_recoil")

注:真 emotions library 首次使用触发 HF 下载(白名单 §4)。
"""
from __future__ import annotations

import logging
from typing import Any

from reachy_mini_dances_library.dance_move import DanceMove

from tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)


# Emotion → DanceMove fallback 映射(emotion 库缺失时用)
EMOTION_TO_DANCE_FALLBACK = {
    "happy": "groovy_sway_and_roll",
    "joy": "groovy_sway_and_roll",
    "sad": "side_to_side_sway",
    "thinking": "chin_lead",
    "greeting": "neck_recoil",
    "hello": "neck_recoil",
    "yes": "yeah_nod",
    "no": "uh_huh_tilt",
    "confused": "head_tilt_roll",
    "dance": "groovy_sway_and_roll",  # 默认 dance → 随机 dance
}


class PlayEmotionTool(Tool):
    """让机器人表达一个情感(happy / sad / thinking / greeting 等)。"""

    name = "play_emotion"
    description = (
        "Express an emotion through a predefined move. "
        "Options: happy, sad, thinking, greeting, yes, no, confused. "
        "Uses SDK emotion library if installed; falls back to dance moves otherwise."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "emotion": {
                "type": "string",
                "description": (
                    "Emotion name. Common values: happy, sad, thinking, greeting, "
                    "yes, no, confused, hello."
                ),
            },
        },
        "required": ["emotion"],
        "additionalProperties": False,
    }

    async def __call__(
        self, deps: ToolDependencies, **kwargs: Any
    ) -> dict[str, Any]:
        emotion = kwargs.get("emotion", "").lower().strip()
        if not emotion:
            return {"error": "emotion 参数必填"}

        # 1. 尝试用 SDK emotions library(决策 16D)
        try:
            from reachy_mini_emotions_library import (  # type: ignore[import-not-found]
                play_emotion as sdk_play_emotion,
            )

            sdk_play_emotion(emotion)
            return {
                "status": "played",
                "emotion": emotion,
                "backend": "reachy_mini_emotions_library",
            }
        except ImportError:
            logger.info(
                "[play_emotion] emotions library 未装,降级到 DanceMove fallback"
            )
        except Exception as e:
            logger.warning(
                f"[play_emotion] SDK play_emotion 失败:{type(e).__name__}: {e},降级到 fallback"
            )

        # 2. Fallback:DanceMove
        dance_name = EMOTION_TO_DANCE_FALLBACK.get(emotion, "neck_recoil")
        try:
            move = DanceMove(dance_name)
            # Bug fix (P7.B): use async_play_move (native async) instead of
            # sync play_move (async_to_sync wrapper). The sync version raises
            # "RuntimeError: You cannot use AsyncToSync in the same thread as
            # an async event loop" when called from within this async __call__.
            await deps.reachy_mini.async_play_move(move)
            return {
                "status": "played",
                "emotion": emotion,
                "fallback_dance": dance_name,
                "backend": "reachy_mini_dances_library",
            }
        except Exception as e:
            logger.exception(f"[play_emotion] fallback failed: {e}")
            return {
                "error": f"play_emotion failed:{type(e).__name__}: {e}",
                "emotion": emotion,
            }
