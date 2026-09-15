from __future__ import annotations

import abc
import asyncio
import importlib
import importlib.util
import inspect
import json
import logging
import sys
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


async def _dispatch_tool_call(tool_name: str, args: dict[str, Any], deps: ToolDependencies) -> dict[str, Any]:
    tool = ALL_TOOLS.get(tool_name)
    if not tool:
        return {"error": f"unknown tool: {tool_name}"}
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
