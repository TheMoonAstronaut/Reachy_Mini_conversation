"""reachymini_conversation.brain.doubao_brain — 豆包 LLM 客户端(P7 真 function calling)。

P4 用 regex 解析"中文括号"作伪 tool call(决策 7 过渡)。
P7 升级为 OpenAI 兼容的真 function calling:
  - payload['tools'] = ALL_TOOL_SPECS(从 tools.core_tools)
  - payload['tool_choice'] = 'auto'
  - 解析 response.choices[0].message.tool_calls
  - 用 dispatch_tool_call 实际执行每个 tool_call
  - 把 tool 结果 follow-up 回 LLM
  - 最终得到带工具上下文的回复
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """LLM 发出的工具调用请求。"""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None  # P7 实装结果


@dataclass
class BrainResult:
    """LLM 查询结果。"""

    reply: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_output: str = ""


# P4 兼容:P4 regex 解析(没用真 function calling 时仍可用)
_TOOL_PATTERN = re.compile(r"（([^）]+)）|\(([^)]+)\)")
_TOOL_NAME_MAP = {
    "舞蹈": "dance",
    "跳舞": "dance",
    "摆头": "move_head",
    "点头": "move_head",
    "摇头": "move_head",
    "停": "stop_dance",
    "不动": "idle_do_nothing",
    "待机": "idle_sway",  # 2026-09-16:待机=呼吸微动序列(原来映射到"完全不动")
    "休息": "idle_sway",
    "待命": "idle_sway",
}


class DoubaoBrain:
    """豆包方舟 Ark LLM 客户端(OpenAI 兼容协议)。

    P7 支持真 function calling(决策 7 升级)。
    用法:
        brain = DoubaoBrain()
        result = await brain.query_async(
            user_msg="打个招呼",
            tool_deps=deps,  # ToolDependencies 实例,None 则跳过工具执行
            messages_history=[],  # 多轮对话历史
        )
    """

    def __init__(self, provider: str = "doubao", cfg: dict[str, Any] | None = None) -> None:
        from reachymini_conversation.config_helper import get_brain_config

        self.provider = provider
        if cfg is None:
            cfg = get_brain_config()
        self.cfg = cfg[provider] if provider in cfg else cfg.get("doubao", cfg)
        self.api_key: str = self.cfg.get("api_key", "")
        self.base_url: str = self.cfg.get("base_url", "https://ark.cn-beijing.volces.com/api/v3")
        self.model: str = self.cfg.get("model", "doubao-seed-character-251128")

    @property
    def is_configured(self) -> bool:
        """是否配置了 API Key(没配 → 走 mock)。"""
        return bool(self.api_key)

    def query(self, user_msg: str, tool_deps: Any = None) -> BrainResult:
        """同步 LLM 查询。"""
        return _run_sync(self.query_async, user_msg, tool_deps)

    async def query_async(
        self,
        user_msg: str,
        tool_deps: Any = None,
        history: list[dict[str, Any]] | None = None,
    ) -> BrainResult:
        """异步 LLM 查询(支持真 function calling,P7)。"""
        if not self.is_configured:
            return BrainResult(
                reply=f"(mock)收到:{user_msg!r}\n(配置 API Key 后接真豆包)",
                raw_output="<mock>",
            )

        # 1. 构建消息
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "你是 Reachy Mini,一个友好、有表现力的机器人助手。"
                    "回复简短,自然,中文。"
                    "需要时调用工具执行动作(舞蹈 / 摆头 / 情感表达等)。"
                    "用户想跳舞时调 dance 工具,不要指定 move(留空随机选舞);"
                    "「欢快/优雅/酷炫」这类形容词不是舞种名,同样留空随机;"
                    "用户说「点点头」时调 dance 工具并指定 move='simple_nod';"
                ),
            },
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_msg})

        # 2. 构建工具 schemas(P7 真 function calling)
        tools_spec = None
        if tool_deps is not None:
            try:
                # tools 包不在 reachymini_conversation/ 里,要 sys.path 加根目录
                # (其实 sys.path 在 app.py 已经配过;但这里 brain 单独 import 也要)
                from tools.core_tools import ALL_TOOL_SPECS

                tools_spec = ALL_TOOL_SPECS
            except ImportError as e:
                logger.debug(f"[Brain] tools.core_tools import failed: {e}")

        # 3. 调 LLM(第一轮)
        try:
            response_data = await self._call_llm(messages, tools_spec)
        except Exception as e:
            logger.exception("[Brain] LLM call failed")
            return BrainResult(
                reply=f"(LLM 错误:{type(e).__name__})",
                raw_output=str(e),
            )

        # 4. 处理 tool_calls(P7)
        tool_calls_done: list[ToolCall] = []
        if response_data.get("tool_calls"):
            for tc in response_data["tool_calls"]:
                tool_call_obj = await self._execute_tool_call(tc, tool_deps)
                tool_calls_done.append(tool_call_obj)

            # 5. 把 tool 结果 follow-up 回 LLM,获得最终回复
            try:
                # 把 assistant 消息(含 tool_calls) 加进去
                messages.append(
                    {
                        "role": "assistant",
                        "content": response_data.get("content", ""),
                        "tool_calls": response_data["tool_calls"],
                    }
                )
                # 加每个 tool 的 result
                for tc_obj, tc_raw in zip(
                    tool_calls_done, response_data["tool_calls"], strict=False
                ):
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_raw["id"],
                            "content": json.dumps(
                                tc_obj.result or {"error": "no result"}, ensure_ascii=False
                            ),
                        }
                    )
                # 第二轮 LLM 调用
                final_response = await self._call_llm(messages, tools_spec)
                reply_text = final_response.get("content", "")
                raw = final_response.get("raw", "")
            except Exception as e:
                logger.warning(f"[Brain] follow-up LLM call failed: {e}")
                # fallback:用第一轮的 raw content
                reply_text = response_data.get("content", "")
                raw = response_data.get("raw", "")
        else:
            reply_text = response_data.get("content", "")
            raw = response_data.get("raw", "")

        return BrainResult(reply=reply_text, tool_calls=tool_calls_done, raw_output=raw)

    async def _call_llm(
        self, messages: list[dict[str, Any]], tools_spec: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        """调一次豆包 Ark,返回解析后的 dict。

        返回:
            {
              "content": str,        # 文本回复
              "tool_calls": [...],   # OpenAI 格式(如果有)
              "raw": str,            # 原始 JSON 字符串
            }
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 512,
        }
        if tools_spec:
            payload["tools"] = tools_spec
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # P7.B fix: timeout 30s → 90s. LLM 偶发慢(网络/服务排队),
        # 30s 太短误报 ReadTimeout。8 工具 round-trip 通常 5-15s,但偶发到 60s+
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        raw = json.dumps(data, ensure_ascii=False)
        choices = data.get("choices", [])
        if not choices:
            return {"content": "", "tool_calls": [], "raw": raw}

        msg = choices[0].get("message", {})
        content = msg.get("content", "") or ""
        tool_calls_raw = msg.get("tool_calls") or []

        return {
            "content": content,
            "tool_calls": tool_calls_raw,
            "raw": raw,
        }

    async def _execute_tool_call(self, tc_raw: dict[str, Any], tool_deps: Any) -> ToolCall:
        """执行一个 tool_call,把结果填回 ToolCall。"""
        func = tc_raw.get("function", {})
        name = func.get("name", "")
        args_str = func.get("arguments", "{}")

        try:
            args = json.loads(args_str) if isinstance(args_str, str) else args_str
        except json.JSONDecodeError:
            args = {}

        tool_call_obj = ToolCall(name=name, arguments=args)

        if tool_deps is None:
            tool_call_obj.result = {"error": "no tool_deps provided"}
            return tool_call_obj

        try:
            # 调 tools.dispatch_tool_call(deps, name, args_json)
            import tools.core_tools as core_tools

            result = await core_tools.dispatch_tool_call(
                tool_name=name,
                args_json=json.dumps(args, ensure_ascii=False),
                deps=tool_deps,
            )
            tool_call_obj.result = result
        except Exception as e:
            logger.warning(f"[Brain] tool {name} failed: {e}")
            tool_call_obj.result = {"error": f"{type(e).__name__}: {e}"}

        return tool_call_obj


# 兼容旧 API:同步包装
def _run_sync(coro_fn, *args, **kwargs):  # type: ignore[no-untyped-def]
    """在 sync 上下文跑 async coro(新 event loop)。"""
    import asyncio
    import concurrent.futures

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(asyncio.run, coro_fn(*args, **kwargs))
                return future.result()
        else:
            return loop.run_until_complete(coro_fn(*args, **kwargs))
    except RuntimeError:
        return asyncio.run(coro_fn(*args, **kwargs))


# P4 兼容:旧 regex 解析(P4 测试 + 兼容逻辑)
def _parse_tool_calls(text: str) -> tuple[str, list[ToolCall]]:
    """(P4 旧)regex 解析"中文括号" → tool calls。P7 主流程不再用。"""
    tool_calls: list[ToolCall] = []
    for match in _TOOL_PATTERN.finditer(text):
        action = (match.group(1) or match.group(2) or "").strip()
        tool_name = _map_action_to_tool(action)
        if tool_name:
            tool_calls.append(ToolCall(name=tool_name, arguments={"raw": action}))
    cleaned = _TOOL_PATTERN.sub("", text).strip()
    return cleaned, tool_calls


def _map_action_to_tool(action: str) -> str | None:
    for key, tool in _TOOL_NAME_MAP.items():
        if key in action:
            return tool
    return None
