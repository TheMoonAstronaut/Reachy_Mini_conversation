"""config_helper — 桥接根目录的 config.py 给子包用。

为什么:config.py 在根目录(历史原因,保留向后兼容),
子包用 sys.path hack 直接 import 不优雅。

这个模块:
  - 在子包首次调用时把 PROJECT_ROOT 加 sys.path
  - 然后 `import config` 拿到根目录的 config
  - 暴露 get_brain_config() / get_asr_config() / get_tts_config() /
           get_run_mode() / get_full_config() / reload_config()
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import config as _root_config  # noqa: E402


def get_full_config() -> dict[str, Any]:
    """返回当前完整配置(从 env.json 读 + 默认补全)。"""
    return dict(_root_config._env) if _root_config._env else {}


def get_brain_config() -> dict[str, Any]:
    """返回豆包 LLM 配置:`{"provider": "doubao", "doubao": {"api_key", "base_url", "model"}}`。"""
    return dict(_root_config.BRAIN_CONFIG)


def get_asr_config() -> dict[str, Any]:
    """返回豆包 ASR 配置:`{"api_key", "resource_id", "url"}`。"""
    return dict(_root_config.ASR_CONFIG)


def get_tts_config() -> dict[str, Any]:
    """返回 Edge TTS 配置:`{"voice"}`。"""
    return dict(_root_config.TTS_CONFIG)


def get_run_mode() -> str:
    return _root_config.RUN_MODE


def get_hf_preload_datasets() -> bool:
    return _root_config.HF_PRELOAD_DATASETS


def reload() -> None:
    """重新读 env.json + 刷新派生 config(UI 改完 API Key 后调)。"""
    _root_config.reload_config()
