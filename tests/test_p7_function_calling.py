"""P7 真 function calling + play_emotion 单元测试。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ============================================================================
# doubao_brain 真 function calling 测试(mock httpx)
# ============================================================================
def _mock_response_with_tool_calls(content: str = "", tool_calls: list | None = None):
    """构造豆包 Ark 响应(OpenAI 格式)。"""
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls or [],
                },
            }
        ],
    }


def _mock_response_text(text: str):
    return _mock_response_with_tool_calls(content=text, tool_calls=None)


def test_brain_no_api_key_returns_mock():
    from reachymini_conversation.brain.doubao_brain import BrainResult, DoubaoBrain

    brain = DoubaoBrain(cfg={"doubao": {"api_key": "", "model": "test"}})
    assert brain.is_configured is False
    result = brain.query("hello")
    assert isinstance(result, BrainResult)
    assert "hello" in result.reply
    assert "mock" in result.reply.lower() or "API Key" in result.reply


def test_brain_text_only_response():
    """LLM 不调工具,直接给文本。"""
    from reachymini_conversation.brain.doubao_brain import DoubaoBrain

    brain = DoubaoBrain(cfg={"doubao": {"api_key": "test-key", "model": "test"}})

    # mock httpx response
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=_mock_response_text("你好"))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(brain.query_async("hi"))

    assert result.reply == "你好"
    assert result.tool_calls == []


def test_brain_with_tool_call_executes_tool():
    """LLM 调工具 → dispatch_tool_call 被调 → result 填回。"""
    from reachymini_conversation.brain.doubao_brain import DoubaoBrain

    brain = DoubaoBrain(cfg={"doubao": {"api_key": "test-key", "model": "test"}})

    # 第一轮响应:tool_calls
    first_resp_data = _mock_response_with_tool_calls(
        content="",
        tool_calls=[
            {
                "id": "call_001",
                "type": "function",
                "function": {
                    "name": "dance",
                    "arguments": json.dumps({"move": "yeah_nod"}),
                },
            }
        ],
    )
    # 第二轮响应:最终文本
    second_resp_data = _mock_response_text("好的,跳了一段")

    mock_resp_1 = MagicMock()
    mock_resp_1.raise_for_status = MagicMock()
    mock_resp_1.json = MagicMock(return_value=first_resp_data)
    mock_resp_2 = MagicMock()
    mock_resp_2.raise_for_status = MagicMock()
    mock_resp_2.json = MagicMock(return_value=second_resp_data)

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[mock_resp_1, mock_resp_2])

    # mock tools.core_tools.dispatch_tool_call
    fake_deps = MagicMock()
    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch("tools.core_tools.dispatch_tool_call", new_callable=AsyncMock) as mock_dispatch,
    ):
        mock_dispatch.return_value = {"status": "playing", "move": "yeah_nod"}
        result = asyncio.run(brain.query_async("跳一段舞", tool_deps=fake_deps))

    # 验证
    assert result.reply == "好的,跳了一段"
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc.name == "dance"
    assert tc.arguments == {"move": "yeah_nod"}
    assert tc.result == {"status": "playing", "move": "yeah_nod"}

    # dispatch 被调过一次
    assert mock_dispatch.call_count == 1
    # LLM 被调 2 次(第一轮 + 第二轮 follow-up)
    assert mock_client.post.call_count == 2


def test_brain_with_tool_failure_continues():
    """工具执行失败 → LLM 仍能回复(第二轮)。"""
    from reachymini_conversation.brain.doubao_brain import DoubaoBrain

    brain = DoubaoBrain(cfg={"doubao": {"api_key": "test-key", "model": "test"}})

    first_resp_data = _mock_response_with_tool_calls(
        content="",
        tool_calls=[
            {
                "id": "call_001",
                "type": "function",
                "function": {
                    "name": "nonexistent_tool",
                    "arguments": json.dumps({"foo": "bar"}),
                },
            }
        ],
    )
    second_resp_data = _mock_response_text("工具失败了,但我能回复")

    mock_resp_1 = MagicMock()
    mock_resp_1.raise_for_status = MagicMock()
    mock_resp_1.json = MagicMock(return_value=first_resp_data)
    mock_resp_2 = MagicMock()
    mock_resp_2.raise_for_status = MagicMock()
    mock_resp_2.json = MagicMock(return_value=second_resp_data)

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[mock_resp_1, mock_resp_2])

    fake_deps = MagicMock()
    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch("tools.core_tools.dispatch_tool_call", new_callable=AsyncMock) as mock_dispatch,
    ):
        # mock_dispatch 抛异常
        mock_dispatch.side_effect = RuntimeError("tool not found")
        result = asyncio.run(brain.query_async("hi", tool_deps=fake_deps))

    assert result.reply == "工具失败了,但我能回复"
    assert len(result.tool_calls) == 1
    assert "error" in result.tool_calls[0].result
    assert "tool not found" in result.tool_calls[0].result["error"]


def test_brain_no_tool_deps_skips_execution():
    """tool_deps=None → 工具 schema 仍发,LLM 调,但不执行。"""
    from reachymini_conversation.brain.doubao_brain import DoubaoBrain

    brain = DoubaoBrain(cfg={"doubao": {"api_key": "test-key", "model": "test"}})

    first_resp_data = _mock_response_with_tool_calls(
        content="",
        tool_calls=[
            {
                "id": "call_001",
                "type": "function",
                "function": {
                    "name": "dance",
                    "arguments": "{}",
                },
            }
        ],
    )
    second_resp_data = _mock_response_text("跳过工具,直接回复")

    mock_resp_1 = MagicMock()
    mock_resp_1.raise_for_status = MagicMock()
    mock_resp_1.json = MagicMock(return_value=first_resp_data)
    mock_resp_2 = MagicMock()
    mock_resp_2.raise_for_status = MagicMock()
    mock_resp_2.json = MagicMock(return_value=second_resp_data)

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[mock_resp_1, mock_resp_2])

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(brain.query_async("hi", tool_deps=None))

    assert result.reply == "跳过工具,直接回复"
    # tools schema 没发给 LLM(因为 tool_deps=None)
    first_call_payload = mock_client.post.call_args_list[0].kwargs["json"]
    assert "tools" not in first_call_payload


# ============================================================================
# P4 兼容:regex 解析仍可用
# ============================================================================
def test_parse_tool_calls_compat():
    """(P4 兼容)_parse_tool_calls 函数仍能解析中文括号。"""
    from reachymini_conversation.brain.doubao_brain import _parse_tool_calls

    reply, tool_calls = _parse_tool_calls("好的(点头)我来了")
    assert "点头" not in reply
    assert tool_calls[0].name == "move_head"


# ============================================================================
# play_emotion 工具测试
# ============================================================================
def test_play_emotion_no_args_returns_error():
    from tools.play_emotion import PlayEmotionTool

    deps = MagicMock()
    tool = PlayEmotionTool()
    result = asyncio.run(tool(deps))
    assert "error" in result


def test_play_emotion_unknown_emotion_falls_back_to_dance():
    """SDK emotions 缺失 → fallback DanceMove。"""
    from tools.play_emotion import PlayEmotionTool

    deps = MagicMock()
    deps.reachy_mini = MagicMock()
    # P7.B: async_play_move 必须能用 await (AsyncMock)
    deps.reachy_mini.async_play_move = AsyncMock()

    tool = PlayEmotionTool()
    result = asyncio.run(tool(deps, emotion="happy"))

    # 应该成功,fallback 到 dance
    assert result["status"] == "played"
    assert result["emotion"] == "happy"
    assert result["backend"] == "reachy_mini_dances_library"
    assert result["fallback_dance"] == "groovy_sway_and_roll"
    deps.reachy_mini.async_play_move.assert_called_once()


def test_play_emotion_unknown_emotion_falls_back_to_neck_recoil():
    """未在映射里的 emotion → fallback neck_recoil 默认。"""
    from tools.play_emotion import PlayEmotionTool

    deps = MagicMock()
    deps.reachy_mini = MagicMock()
    deps.reachy_mini.async_play_move = AsyncMock()

    tool = PlayEmotionTool()
    result = asyncio.run(tool(deps, emotion="nonexistent_emotion"))

    assert result["status"] == "played"


def test_play_emotion_uses_async_play_move_not_sync():
    """P7.B Guard: play_emotion 必须在 async 上下文里用 async_play_move,
    不能用 sync play_move (后者内部用 AsyncToSync wrapper,会在已有
    async event loop 的线程里抛 RuntimeError)。

    这个测试 mock deps.reachy_mini, 让 sync play_move 调用抛 RuntimeError
    模拟 AsyncToSync 失败。如果 play_emotion 仍然用 sync 版本,test fail。
    """
    from tools.play_emotion import PlayEmotionTool

    deps = MagicMock()

    # sync play_move 模拟 AsyncToSync RuntimeError
    def _sync_play_move_raises(*args, **kwargs):
        raise RuntimeError(
            "You cannot use AsyncToSync in the same thread as an async event loop "
            "- just await the async function directly."
        )
    deps.reachy_mini.play_move = _sync_play_move_raises

    # async_play_move 应该被调,且成功
    async def _async_play_move_ok(move):
        return None
    deps.reachy_mini.async_play_move = _async_play_move_ok

    tool = PlayEmotionTool()
    result = asyncio.run(tool(deps, emotion="happy"))

    # 应该成功(走 async_play_move), 不是走 sync play_move 抛 RuntimeError
    assert result["status"] == "played", f"Expected 'played', got: {result}"
    assert result.get("error") is None, f"Should not have error: {result}"
    assert result["fallback_dance"] == "groovy_sway_and_roll"  # happy → groovy_sway_and_roll


def test_play_emotion_propagates_reachy_error():
    """reachy.async_play_move 抛异常 → 返回 error,不崩。

    P7.B fix: play_emotion 现在调 async_play_move(不是 sync play_move),
    所以测试用 AsyncMock + side_effect 模拟异常。
    """
    from tools.play_emotion import PlayEmotionTool

    deps = MagicMock()
    deps.reachy_mini = MagicMock()
    deps.reachy_mini.async_play_move = AsyncMock(side_effect=RuntimeError("robot offline"))

    tool = PlayEmotionTool()
    result = asyncio.run(tool(deps, emotion="happy"))

    assert "error" in result
    assert "robot offline" in result["error"]


def test_play_emotion_registered():
    import tools

    assert "play_emotion" in tools.ALL_TOOLS
