"""tests.smoke_test — 冒烟测试。

目标:不连真实 daemon、不连网络,只检查:
  1. SDK 核心 import
  2. 所有 pyproject deps 都能 import
  3. config 能加载默认 env.json(无文件时用默认值)
  4. CLI 能输出帮助(注册成功)
  5. tools 子包自动注册
  6. mediapipe 1.0+ 新 API 可用(手部跟随)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 仓库根目录加入 sys.path,让 config / tools 等能找到
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ============================================================================
# 1. SDK import
# ============================================================================
def test_reachy_mini_version():
    """reachy_mini >= 1.10.0(决策 1)。"""
    import reachy_mini

    assert reachy_mini.__version__ >= "1.10.0", f"reachy_mini too old: {reachy_mini.__version__}"


def test_reachy_mini_sdk_api():
    """SDK 核心 API 存在(P0.4 决策 7 依赖的 API)。"""
    from reachy_mini import ReachyMini

    # P0.4 决策:替代 HeadWobbler / MovementManager 的 API
    for api in ("enable_wobbling", "disable_wobbling", "play_move", "goto_target", "set_target"):
        assert hasattr(ReachyMini, api), f"ReachyMini 缺 {api}"


def test_reachy_mini_dances_library():
    """dances library 能 import(decision 7 保留)。"""
    from reachy_mini_dances_library.collection.dance import AVAILABLE_MOVES
    from reachy_mini_dances_library.dance_move import DanceMove

    assert DanceMove is not None
    assert isinstance(AVAILABLE_MOVES, dict)
    assert len(AVAILABLE_MOVES) > 0, "AVAILABLE_MOVES 应该非空"


# ============================================================================
# 2. 所有 deps 都能 import(决策 6/13 变更后:无 funasr)
# ============================================================================
def test_all_dependencies_importable():
    """pyproject.toml 列出的所有 deps 都应能 import。"""
    import av
    import dotenv  # python-dotenv
    import edge_tts
    import fastapi
    import gradio
    import httpx
    import mediapipe
    import numpy
    import reachy_mini
    import scipy
    import soundfile
    import uvicorn
    import websockets

    assert all(
        [
            av,
            dotenv,
            edge_tts,
            fastapi,
            gradio,
            httpx,
            mediapipe,
            numpy,
            reachy_mini,
            scipy,
            soundfile,
            uvicorn,
            websockets,
        ]
    ), "all deps must be importable"


def test_numpy_version():
    """numpy >= 2.2.5(reachy_mini 硬要求)。"""
    import numpy

    v = numpy.__version__
    parts = tuple(int(x) for x in v.split(".")[:2])
    assert parts >= (2, 2), f"numpy too old: {v} (need >=2.2.5)"


def test_mediapipe_new_api():
    """mediapipe 1.0+ 用 tasks.python.vision(P6 HandLandmarker 准备)。"""
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    assert python is not None
    assert hasattr(vision, "HandLandmarker"), "P6 需要 vision.HandLandmarker"


# ============================================================================
# 3. config 加载(env.json 不存在 / 存在 / 损坏)
# ============================================================================
def test_config_default_when_env_missing(tmp_path, monkeypatch):
    """env.json 不存在时,config 用默认值。"""
    monkeypatch.setenv("HOME", str(tmp_path))  # 隔离 ~/.reachymini
    # 强制重载
    import importlib

    import config
    import reachymini_conversation.utils.env_loader

    importlib.reload(reachymini_conversation.utils.env_loader)
    importlib.reload(config)

    assert config.RUN_MODE == "pure_sim"
    assert config.HF_PRELOAD_DATASETS is False
    assert config.BRAIN_CONFIG["provider"] == "doubao"
    assert config.BRAIN_CONFIG["doubao"]["model"] == "doubao-seed-character-251128"
    assert config.TTS_CONFIG["voice"] == "zh-CN-XiaoxiaoNeural"


def test_config_loads_from_env_json(tmp_path, monkeypatch):
    """env.json 存在时,正确加载。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    env_path = tmp_path / ".reachymini" / "env.json"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(
        json.dumps(
            {
                "doubao_llm": {"api_key": "test-llm-key", "model": "test-model"},
                "doubao_asr": {"api_key": "test-asr-key"},
                "run_mode": "real_plus_sim",
                "hf_preload_datasets": True,
            }
        )
    )

    import importlib

    import config
    import reachymini_conversation.utils.env_loader

    importlib.reload(reachymini_conversation.utils.env_loader)
    importlib.reload(config)

    assert config.BRAIN_CONFIG["doubao"]["api_key"] == "test-llm-key"
    assert config.BRAIN_CONFIG["doubao"]["model"] == "test-model"
    assert config.ASR_CONFIG["api_key"] == "test-asr-key"
    assert config.RUN_MODE == "real_plus_sim"
    assert config.HF_PRELOAD_DATASETS is True


def test_config_handles_corrupted_env(tmp_path, monkeypatch):
    """env.json 损坏时不报错,回退默认。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    env_path = tmp_path / ".reachymini" / "env.json"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("{ not valid json")

    import importlib

    import config
    import reachymini_conversation.utils.env_loader

    importlib.reload(reachymini_conversation.utils.env_loader)
    importlib.reload(config)

    # 默认值,不抛异常
    assert config.RUN_MODE == "pure_sim"
    assert config.BRAIN_CONFIG["doubao"]["api_key"] == ""


def test_config_no_funasr_block():
    """决策 6/13 变更:config 不再有 FUNASR_CONFIG。"""
    import config

    assert not hasattr(config, "FUNASR_CONFIG"), "FUNASR_CONFIG 应已被移除"


# ============================================================================
# 4. CLI shim 工作
# ============================================================================
def test_reachy_mini_conversation_help():
    """reachy-mini-conversation --help 输出正常(CLI 注册成功)。"""
    import subprocess

    result = subprocess.run(
        ["reachy-mini-conversation", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"--help 失败: {result.stderr}"
    assert "Reachy Mini Conversation" in result.stdout
    assert "--ui" in result.stdout
    assert "--real" in result.stdout


def test_python_m_reachy_mini_help():
    """python -m reachymini_conversation --help 输出正常。"""
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "reachymini_conversation", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"--help 失败: {result.stderr}"
    assert "Reachy Mini Conversation" in result.stdout


# ============================================================================
# 5. tools 子包自动注册(P0.4 决策)
# ============================================================================
def test_tools_autoregister():
    """ALL_TOOLS 应有 5 个工具。"""
    import tools

    expected = {"dance", "stop_dance", "move_head", "idle_do_nothing", "look_at_sound"}
    actual = set(tools.ALL_TOOLS.keys())
    assert expected <= actual, f"missing tools: {expected - actual}"


def test_tools_schemas_generated():
    """ALL_TOOL_SPECS 是 LLM function calling schema 格式(OpenAI / Ark 标准)。"""
    import tools

    assert len(tools.ALL_TOOL_SPECS) == len(tools.ALL_TOOLS)
    for spec in tools.ALL_TOOL_SPECS:
        # OpenAI / Ark 标准:{"type": "function", "function": {"name", "description", "parameters"}}
        assert spec["type"] == "function"
        assert "function" in spec
        func = spec["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func


# ============================================================================
# 6. 子包结构
# ============================================================================
def test_subpackage_skeleton():
    """reachymini_conversation 子包骨架完整。"""

    # app.py 必须有 main()
    from reachymini_conversation.app import main

    assert callable(main)


# ============================================================================
# 7. 网络白名单(config):无意外远程端点
# ============================================================================
def test_no_unexpected_remote_endpoints():
    """config 白名单:ASR/LLM 端点与项目声明一致(静态检查,防回归改域名)。"""
    import config

    # ASR 必须在白名单
    assert "openspeech.bytedance.com" in config.ASR_CONFIG["url"]
    # LLM base_url 必须在白名单
    assert "ark.cn-beijing.volces.com" in config.BRAIN_CONFIG["doubao"]["base_url"]
