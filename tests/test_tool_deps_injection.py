"""tool_deps 注入回归测试(2026-09-16 伪调用 bug)。

事故:real 语音路径(RealVoiceLoop)的共享 pipeline 从未注入 tool_deps,
LLM 无工具可调,只能把"调用 dance 工具"当文本念出来,机器人不动。

保护:set_tool_deps → get_tool_deps_global 通道(app.py 注入路径依赖它)。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from reachymini_conversation import web_ui  # noqa: E402


class TestToolDepsChannel:
    def setup_method(self) -> None:
        web_ui.set_tool_deps(None)

    def teardown_method(self) -> None:
        web_ui.set_tool_deps(None)

    def test_unset_returns_none(self) -> None:
        assert web_ui.get_tool_deps_global() is None

    def test_set_then_get(self) -> None:
        sentinel = object()
        web_ui.set_tool_deps(sentinel)
        assert web_ui.get_tool_deps_global() is sentinel
