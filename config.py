"""Configuration for Reachy Mini Conversation system.

P0.3 改造点:
    - 不再硬编码 API Key,改为读 ~/.reachymini/env.json(由 env_loader 加载)
    - 文件不存在时使用 DEFAULT_ENV(env_loader 提供)
    - 提供 reload_config() 用于运行时刷新(UI 改完 API Key 重启或 reload)
    - 保留旧的访问模式 BRAIN_CONFIG / TTS_CONFIG / ASR_CONFIG / AUDIO_CONFIG
      以兼容 legacy 模块(main.py, brain.py, asr.py 等)

P3 改造点(待做):
    - 提供 dump_config() 返回当前完整配置(给 UI 设置面板显示)
    - 提供 update_config(partial) 增量更新
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from reachymini_conversation.utils.env_loader import (
    DEFAULT_ENV,
    get_env_json_path,
    load_env,
    reload_env,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 路径
# ============================================================================
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PROFILES_DIRECTORY = PROJECT_ROOT / "profiles"


def reload_config() -> None:
    """重新加载 env.json 并刷新派生配置(给 UI 改 API Key 后用)。"""
    global _env, BRAIN_CONFIG, ASR_CONFIG, TTS_CONFIG, RUN_MODE, HF_PRELOAD_DATASETS
    reload_env()
    _env = load_env()
    BRAIN_CONFIG = _build_brain_config()
    ASR_CONFIG = _build_asr_config()
    TTS_CONFIG = _build_tts_config()
    RUN_MODE = _env.get("run_mode", DEFAULT_ENV["run_mode"])
    HF_PRELOAD_DATASETS = bool(_env.get("hf_preload_datasets", DEFAULT_ENV["hf_preload_datasets"]))
    logger.info("config reloaded from %s", get_env_json_path())


# ============================================================================
# 加载用户配置(env.json)
# ============================================================================
_env: dict[str, Any] = load_env()


def _get(key: str, *path: str, default: Any = None) -> Any:
    """从 _env 取嵌套值:config.get('a', 'b', 'c') -> _env['a']['b']['c']。"""
    cur: Any = _env
    for k in (key, *path):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _build_brain_config() -> dict[str, Any]:
    """构建 BRAIN_CONFIG(向后兼容)。"""
    return {
        "provider": _get("provider", default=DEFAULT_ENV.get("provider", "doubao")),
        "doubao": {
            "api_key": _get("doubao_llm", "api_key", default=""),
            "base_url": _get("doubao_llm", "base_url", default=DEFAULT_ENV["doubao_llm"]["base_url"]),
            "model": _get("doubao_llm", "model", default=DEFAULT_ENV["doubao_llm"]["model"]),
        },
    }


def _build_asr_config() -> dict[str, Any]:
    """构建 ASR_CONFIG(向后兼容)。"""
    return {
        "api_key": _get("doubao_asr", "api_key", default=""),
        "resource_id": _get(
            "doubao_asr", "resource_id", default=DEFAULT_ENV["doubao_asr"]["resource_id"]
        ),
        "url": "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream",
    }


def _build_tts_config() -> dict[str, Any]:
    """构建 TTS_CONFIG(向后兼容)。"""
    return {
        "voice": _get("edge_tts", "voice", default=DEFAULT_ENV["edge_tts"]["voice"]),
    }


# ============================================================================
# 派生配置(模块加载时一次性计算,UI 改完调 reload_config())
# ============================================================================
BRAIN_CONFIG: dict[str, Any] = _build_brain_config()
ASR_CONFIG: dict[str, Any] = _build_asr_config()
TTS_CONFIG: dict[str, Any] = _build_tts_config()
RUN_MODE: str = os.environ.get(
    "REACHYMINI_RUN_MODE",
    _env.get("run_mode", DEFAULT_ENV["run_mode"]),
)
HF_PRELOAD_DATASETS: bool = bool(
    _env.get("hf_preload_datasets", DEFAULT_ENV["hf_preload_datasets"])
)
# 决策 6/13 变更(2025-09-09):移除 FUNASR_CONFIG,改用豆包 ASR(WebSocket 流式)


# ============================================================================
# 本地常量(不入 env.json)
# ============================================================================
AUDIO_CONFIG: dict[str, Any] = {
    "silence_timeout": 2.0,
}

SYSTEM_PROMPT: str = """You are Reachy Mini, a friendly and expressive robot assistant.

## Your Identity
- You are a warm, caring robot companion with a playful personality
- You have a physical body that can move, dance, and express emotions
- You communicate through speech and physical gestures

## Available Actions
You have access to tools that control your physical body:

1. **dance** - Play a dance move. You have many dances available. Use this when you want to celebrate, express joy, or entertain the user.

2. **move_head** - Move your head in a direction (left, right, up, down, front). Use this for natural head movements during conversation.

3. **stop_dance** - Stop any ongoing dance move.

4. **idle_do_nothing** - Stay still and silent.

5. **look_at_sound** - (P5+) Turn your head toward a sound source.

6. **start_hand_follow** / **stop_hand_follow** - (P6+) Enable/disable tracking the user's hand with your eyes.

## Guidelines
- Keep responses warm and conversational
- Use physical actions to express emotions
- Be playful but not annoying
- Actions are non-blocking - you can speak while dancing"""


# ============================================================================
# 自检
# ============================================================================
def _self_check() -> None:
    """启动时自检:API Key 缺失就 WARNING。"""
    if not BRAIN_CONFIG["doubao"]["api_key"]:
        logger.warning(
            "BRAIN api_key 未配置。请编辑 %s 或用 UI 设置面板填写。",
            get_env_json_path(),
        )
    if not ASR_CONFIG["api_key"]:
        logger.warning(
            "ASR api_key 未配置(豆包 WebSocket 流式,需要 .reachymini/env.json 填 doubao_asr.api_key)。"
        )
    if RUN_MODE not in {"pure_sim", "real_plus_sim", "pure_real"}:
        logger.warning(f"RUN_MODE='{RUN_MODE}' 不在白名单内,应为 pure_sim | real_plus_sim")


_self_check()