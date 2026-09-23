from __future__ import annotations

import abc
import asyncio
import importlib
import importlib.util
import inspect
import json
import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reachy_mini import ReachyMini

logger = logging.getLogger(__name__)


ALL_TOOLS: dict[str, Tool] = {}
ALL_TOOL_SPECS: list[dict[str, Any]] = []
_TOOLS_INITIALIZED = False


class MissingToolFileError(FileNotFoundError):
    pass


def get_concrete_subclasses(base: type[Tool]) -> list[type[Tool]]:
    result: list[type[Tool]] = []
    for cls in base.__subclasses__():
        if not inspect.isabstract(cls):
            result.append(cls)
        result.extend(get_concrete_subclasses(cls))
    return result


@dataclass
class ToolDependencies:
    reachy_mini: ReachyMini
    movement_manager: Any
    camera_worker: Any | None = None
    vision_processor: Any | None = None
    # P0.4:删除 head_wobbler 字段(SDK enable_wobbling() 已覆盖)
    motion_duration_s: float = 1.0
    # P6:手部跟随器(可选,LLM 工具调用 start/stop_hand_follow)
    hand_follower: Any | None = None


class Tool(abc.ABC):
    name: str
    description: str
    parameters_schema: dict[str, Any]

    def spec(self) -> dict[str, Any]:
        # OpenAI / Ark Chat Completions 标准格式(P7 真 function calling)
        # 必须用 "function" 嵌套(否则 Ark 报 MissingParameter)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

    @abc.abstractmethod
    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError


def _load_module_from_file(module_name: str, file_path: Path) -> None:
    if not file_path.is_file():
        raise MissingToolFileError(f"tool file not found at {file_path}")

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if not (spec and spec.loader):
        raise ModuleNotFoundError(f"Cannot create spec for {file_path}")
    module = importlib.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise


def _initialize_tools() -> None:
    global ALL_TOOLS, ALL_TOOL_SPECS, _TOOLS_INITIALIZED

    if _TOOLS_INITIALIZED:
        return

    # P0.4:删除 tool_constants 依赖(SystemTool enum 已废弃)
    # 显式声明要 import 的工具模块(供 get_concrete_subclasses 扫描子类)
    tool_names = [
        "dance",
        "stop_dance",
        "move_head",
        "idle_do_nothing",
        "idle_sway",  # 2026-09-16:呼吸式待机微动(用户点名的"待机动作")
        "look_at_sound",  # P5
        "start_hand_follow",  # P6
        "stop_hand_follow",  # P6
        "play_emotion",  # P7 决策 16D
    ]

    for tool_name in tool_names:
        try:
            importlib.import_module(f"tools.{tool_name}")
        except ModuleNotFoundError:
            pass

    ALL_TOOLS = {cls.name: cls() for cls in get_concrete_subclasses(Tool)}
    ALL_TOOL_SPECS = [tool.spec() for tool in ALL_TOOLS.values()]

    for tool_name, tool in ALL_TOOLS.items():
        logger.info(f"tool registered: {tool_name} - {tool.description}")

    _TOOLS_INITIALIZED = True


_initialize_tools()


def get_tool_specs(exclusion_list: list[str] | None = None) -> list[dict[str, Any]]:
    # 默认 None 避免可变默认参数(B006);空列表等价于不过滤
    excluded = exclusion_list or []
    return [spec for spec in ALL_TOOL_SPECS if spec.get("name") not in excluded]


def _safe_load_obj(args_json: str) -> dict[str, Any]:
    try:
        parsed_args = json.loads(args_json or "{}")
        return parsed_args if isinstance(parsed_args, dict) else {}
    except Exception:
        logger.warning("bad args_json=%r", args_json)
        return {}


# 表演类工具(2026-09-18 响应延迟治理):这些工具执行的是"机器人表演动作"
# (dance/表情/待机摇摆/转头),底层 async_play_move 会阻塞到动作播完
# (实测 idle_sway×3 cycles ≈ 24s,dance 10-20s)。用户日志证据:ASR 文本
# → 工具调用 → TTS 合成 间隔最大 39s,其中绝大部分是动作阻塞 —— 语音
# 回复被排在动作之后,感知为"思考等待过久"。
# 改为后台 daemon 线程执行(线程内 asyncio.run):LLM 轮次立即拿到
# "started" 继续出回复,TTS 合成与动作并行。
# 兼容性依据:idle_breath 控制器早就用"独立线程 + asyncio.run"驱动
# 同一套 MirroredToolTarget/SDK async_play_move(2026-09-16 起生产验证);
# 而 asyncio.create_task 不行 —— real_voice/web_ui 每轮 run_audio/run_text
# 都新建临时 loop(asyncio.run),turn 结束 loop 即销毁,task 会被连坐。
# 代价:动作与播报 wobbler 可能短暂并发写头部(daemon 仲裁),观感问题
# 若出现再治理;响应速度优先。SDK move 不支持中途取消(stop_dance 语义
# 本来就是"不再起下一个")。
_BG_PERFORM_TOOLS = frozenset(
    {"dance", "play_emotion", "idle_sway", "move_head", "look_at_sound"}
)
# 同名后台线程注册表:同工具上一个还在跑时,新调用直接跳过(防叠跳)
_BG_THREADS: dict[str, threading.Thread] = {}


def _run_tool_background(tool_name: str, tool: Any, args: dict[str, Any], deps: ToolDependencies) -> dict[str, Any]:
    """把表演类工具放入后台 daemon 线程,立即返回 started(见上方注释)。"""

    old = _BG_THREADS.get(tool_name)
    if old is not None and old.is_alive():
        logger.info("[tool-bg] %s 仍在执行,跳过重复调用", tool_name)
        return {"status": "already_running", "message": f"{tool_name} 正在执行中"}

    def _worker() -> None:
        try:
            result = asyncio.run(tool(deps, **args))
            logger.info("[tool-bg] %s 后台执行完成: %s", tool_name, str(result)[:120])
        except asyncio.CancelledError:
            logger.info("[tool-bg] %s 后台任务被取消", tool_name)
        except Exception as e:
            logger.exception("[tool-bg] %s 后台执行失败: %s", tool_name, e)

    th = threading.Thread(target=_worker, daemon=True, name=f"tool-bg-{tool_name}")
    _BG_THREADS[tool_name] = th
    th.start()
    return {"status": "started", "message": f"{tool_name} 已在后台开始执行"}


def is_tool_running(tool_name: str) -> bool:
    """指定工具是否有后台任务在跑(stop_dance 等状态查询用)。"""
    t = _BG_THREADS.get(tool_name)
    return t is not None and t.is_alive()


async def _dispatch_tool_call(tool_name: str, args: dict[str, Any], deps: ToolDependencies) -> dict[str, Any]:
    tool = ALL_TOOLS.get(tool_name)
    if not tool:
        return {"error": f"unknown tool: {tool_name}"}
    # 表演类工具后台化(见 _BG_PERFORM_TOOLS 注释)
    if tool_name in _BG_PERFORM_TOOLS:
        return _run_tool_background(tool_name, tool, args, deps)
    try:
        return await tool(deps, **args)
    except asyncio.CancelledError:
        logger.info("Tool cancelled: %s", tool_name)
        return {"error": "Tool cancelled"}
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        logger.exception("Tool error in %s: %s", tool_name, msg)
        return {"error": msg}


async def dispatch_tool_call(tool_name: str, args_json: str, deps: ToolDependencies) -> dict[str, Any]:
    return await _dispatch_tool_call(tool_name, _safe_load_obj(args_json), deps)
