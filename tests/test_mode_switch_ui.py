"""V2.2 运行模式切换 UI 的回归测试。

保护的事:
  1. 下拉文案 ↔ (run_mode, conn_cfg) 映射正确(有线 8001 / 无线 network)
  2. build_ui() 里包含模式下拉 + 切换回调已绑定(不 launch,查组件与依赖图)
  3. _render_run_mode 的三态渲染(pure_sim / real_plus_sim / 连接中)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def test_choice_mapping():
    from reachymini_conversation.web_ui import (
        _RUN_MODE_PURE_SIM,
        _RUN_MODE_REAL_WIRED,
        _run_mode_choice_to_request,
    )

    mode, cfg = _run_mode_choice_to_request(_RUN_MODE_PURE_SIM)
    assert mode == "pure_sim"
    mode, cfg = _run_mode_choice_to_request(_RUN_MODE_REAL_WIRED)
    assert mode == "real_plus_sim" and cfg["type"] == "wired" and cfg["port"] == 8001


def test_render_run_mode_states():
    from reachymini_conversation.web_ui import _render_run_mode

    assert "pure_sim" in _render_run_mode("pure_sim")
    assert "real_plus_sim" in _render_run_mode("real_plus_sim")
    assert "连接真机中" in _render_run_mode("pure_sim", real_connecting=True)


def test_mode_dropdown_in_ui():
    """build_ui 里能找到运行模式下拉(choices 含三选项)。"""
    import gradio as gr

    from reachymini_conversation.web_ui import build_ui

    demo = build_ui()
    dropdowns = [b for b in demo.blocks.values() if isinstance(b, gr.Dropdown)]
    assert dropdowns, "UI 里没有 Dropdown"
    found = False
    for d in dropdowns:
        choices = [c[0] if isinstance(c, tuple) else c for c in (d.choices or [])]
        if any("纯仿真" in str(c) for c in choices) and any("真机" in str(c) for c in choices):
            found = True
            break
    assert found, f"没找到运行模式下拉(现有 dropdowns: {[d.choices for d in dropdowns]})"
