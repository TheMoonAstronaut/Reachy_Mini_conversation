"""env_loader:加载 ~/.reachymini/env.json 的工具。

约定:
    - 用户级配置路径:`~/.reachymini/env.json`(Windows 下为 `%USERPROFILE%\\.reachymini\\env.json`)。
    - 文件不存在时返回空 dict,调用方负责用默认值补全。
    - 文件存在但解析失败时打 WARNING 并返回空 dict。
    - 自动创建父目录(写时)。
    - 文件权限建议 0600(仅当前用户可读写),写入时强制 chmod。

API:
    - load_env() -> dict                 读取(自动 reload 缓存)
    - get_env_json_path() -> Path         路径(用于 UI 显示)
    - save_env(data: dict) -> None        写入(创建父目录 + chmod 0600)
    - reload_env() -> dict                强制重新读(供运行时改 env.json)

为什么用 JSON 而不是 .env:
    - 嵌套结构(LLM/ASR/TTS/FunASR 各自有多个字段)
    - UI 直接编辑(JSON 容易解析)
    - 避免和 python-dotenv 变量名冲突
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------- 路径 ----------
def get_env_json_path() -> Path:
    """用户级 env.json 路径(跨平台)。"""
    return Path.home() / ".reachymini" / "env.json"


# ---------- 加载 ----------
_env_cache: dict[str, Any] | None = None


def load_env(*, force_reload: bool = False) -> dict[str, Any]:
    """读取 env.json。

    Args:
        force_reload: 强制重新读文件,跳过缓存。

    Returns:
        解析后的 dict。文件不存在 / 解析失败时返回空 dict(不抛异常)。
    """
    global _env_cache
    if _env_cache is not None and not force_reload:
        return _env_cache

    path = get_env_json_path()
    if not path.exists():
        logger.debug(f"env.json not found at {path}, returning empty dict")
        _env_cache = {}
        return _env_cache

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning(f"env.json root is not dict (got {type(data).__name__}), ignoring")
            data = {}
        _env_cache = data
        logger.info(f"env.json loaded: {len(data)} top-level keys")
        return _env_cache
    except json.JSONDecodeError as e:
        logger.warning(f"env.json parse error at {path}: {e}")
        _env_cache = {}
        return _env_cache
    except OSError as e:
        logger.warning(f"env.json read error at {path}: {e}")
        _env_cache = {}
        return _env_cache


def reload_env() -> dict[str, Any]:
    """强制重新读取 env.json。"""
    return load_env(force_reload=True)


# ---------- 保存 ----------
def save_env(data: dict[str, Any]) -> Path:
    """写 env.json(创建父目录,设置 0600 权限)。

    Args:
        data: 要写入的 dict(全量覆盖,不增量合并)。

    Returns:
        写入的文件路径。

    Raises:
        OSError: 写入失败时。
    """
    path = get_env_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # 设置 0600 权限(敏感信息,只有当前用户能读)
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows 上 chmod 部分位不支持,忽略
        logger.debug(f"chmod 0600 not supported on {path}")

    logger.info(f"env.json saved: {len(data)} top-level keys -> {path}")
    reload_env()
    return path


# ---------- schema 校验 ----------
def validate_env(data: dict[str, Any]) -> list[str]:
    """校验 env.json 的字段是否在白名单内。

    Returns:
        未识别字段名列表(空 = 全部合法)。
    """
    known_keys = {
        "doubao_llm",
        "doubao_asr",
        "edge_tts",
        "run_mode",
        "hf_preload_datasets",
        # 决策 6/13 变更(2025-09-09):移除 funasr
        "provider",
    }
    unknown = [k for k in data.keys() if k not in known_keys]
    if unknown:
        logger.warning(f"env.json contains unknown keys: {unknown}")
    return unknown


# ---------- 默认值 ----------
DEFAULT_ENV: dict[str, Any] = {
    "doubao_llm": {
        "api_key": "",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-character-251128",
    },
    "doubao_asr": {
        "api_key": "",
        "resource_id": "volc.seedasr.sauc.duration",
    },
    "edge_tts": {
        "voice": "zh-CN-XiaoxiaoNeural",
    },
    "run_mode": "pure_sim",
    "hf_preload_datasets": False,
    # 决策 6/13 变更(2025-09-09):移除 funasr 块,改用豆包 ASR WebSocket 流式
}


def get_default(key: str) -> Any:
    """取 DEFAULT_ENV 的某个 key(供 config.py 用)。"""
    return DEFAULT_ENV.get(key)
