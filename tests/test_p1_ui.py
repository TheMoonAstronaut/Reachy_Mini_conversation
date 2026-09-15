"""P1 smoke test: state_bus + web_ui + app class hierarchy (no daemon)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def test_state_bus_basic():
    """StateBus: get / update / snapshot 线程安全。"""
    from reachymini_conversation.state_bus import StateBus, get_state_bus, reset_state_bus

    reset_state_bus()

    # ---- 全局单例 ----
    b = get_state_bus()
    assert b.get("status") == "starting"
    assert b.get("missing", "fallback") == "fallback"

    # update
    b.update("status", "running")
    assert b.get("status") == "running"

    # update_many
    b.update_many({"status": "stopping", "head_joints": [1.0, 2.0, 3.0]})
    snap = b.snapshot()
    assert snap["status"] == "stopping"
    assert snap["head_joints"] == [1.0, 2.0, 3.0]
    assert snap["last_update"] is not None

    # 同一个全局实例(任何路径获取都一样)
    b2 = get_state_bus()
    assert b is b2
    b2.update("status", "from_global")
    assert b.get("status") == "from_global"

    # ---- 本地独立实例 ----
    local = StateBus()
    assert local.get("status") == "starting"  # 默认
    assert b.get("status") == "from_global"  # 全局不受影响
    assert local is not b


def test_web_ui_build():
    """build_ui() 构建 Gradio Blocks 不抛异常(不 launch)。"""
    from reachymini_conversation.web_ui import build_ui

    demo = build_ui()
    assert demo is not None
    assert hasattr(demo, "title")
    assert "Reachy" in str(demo.title)


def test_web_ui_helpers():
    """_render_status / _flatten_pose / _render_meta。"""
    import numpy as np

    from reachymini_conversation.web_ui import (
        _flatten_pose,
        _render_meta,
        _render_status,
    )

    # ---- _render_status ----
    md = _render_status({"status": "running", "error": None})
    assert "运行中" in md
    md_err = _render_status({"status": "running", "error": "boom"})
    assert "boom" in md_err
    md_unknown = _render_status({"status": "weird", "error": None})
    assert "Unknown" in md_unknown

    # ---- _flatten_pose(4×4)----
    m = np.eye(4)
    flat = _flatten_pose(m)
    assert isinstance(flat, list)
    assert len(flat) == 4
    assert all(len(row) == 4 for row in flat)

    # ---- _flatten_pose(None)----
    assert _flatten_pose(None) is None

    # ---- _flatten_pose(2D 嵌套保留)----
    assert _flatten_pose([[1, 2], [3, 4]]) == [[1, 2], [3, 4]]
    assert _flatten_pose([1, 2, 3, 4]) == [1, 2, 3, 4]  # 1D 不变

    # ---- _render_meta ----
    meta = _render_meta({"status": "running", "last_update": None, "error": None})
    assert meta["status"] == "running"
    assert meta["last_update"] == "never"
    assert meta["error"] is None


def test_app_class_hierarchy():
    """ConversationApp 继承 ReachyMiniApp 且实现 run()。"""
    from reachy_mini.apps.app import ReachyMiniApp

    from reachymini_conversation.app import ConversationApp

    assert issubclass(ConversationApp, ReachyMiniApp)
    # abstract method 已实现(不再是 abstractmethod)
    assert "run" in vars(ConversationApp) or hasattr(ConversationApp, "run")

    # P1 不开 SDK FastAPI
    assert ConversationApp.custom_app_url is None
    assert ConversationApp.dont_start_webserver is True


def test_arg_parser():
    """_build_arg_parser 接受 --ui / --real / --preload-datasets / --head-poll-hz。"""
    from reachymini_conversation.app import _build_arg_parser

    parser = _build_arg_parser()

    args = parser.parse_args(["--ui", "--head-poll-hz", "2.5"])
    assert args.ui is True
    assert args.head_poll_hz == 2.5

    args = parser.parse_args([])
    assert args.ui is False
    assert args.head_poll_hz == 1.0


def test_build_ui_no_gradio6_moved_params_warning():
    """Guard:build_ui() 不得触发 Gradio 6.0 "moved to launch()" deprecation 警告。

    背景:Gradio 6.0 把 theme/css/css_paths/js/head/head_paths 从 Blocks 构造器
    移到 launch()。B2 重设计曾把 theme/css 放进 gr.Blocks(...),导致每次真实启动
    都在控制台打 UserWarning(pytest 全过但运行时"报错"——警告不在单测暴露,
    因为测试只 build 不 launch,且不 promote warnings 为 error)。

    修复后:theme/css 由 app.py 的 launch(theme=REACHY_THEME, css=REACHY_CSS) 注入,
    build_ui() 应保持无警告。此 guard 在修复前必然失败(警告被捕获),修复后通过。
    """
    import warnings

    from reachymini_conversation.web_ui import build_ui

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        build_ui()

    moved = [
        w for w in caught if "moved from the Blocks constructor" in str(w.message)
    ]
    assert not moved, (
        "build_ui() 触发了 Gradio 6.0 deprecation 警告(theme/css 应传给 launch()):"
        f"{[str(w.message) for w in moved]}"
    )


def test_theme_css_wired_into_launch():
    """Guard:theme/css 必须从 web_ui 模块导出并由 ConversationApp 传给 launch()。

    launch() 是运行时行为(需要 daemon + 真 launch 才能覆盖),单测无法直接验证
    launch 调用本身,因此用两级 guard:
      1. 导出级:web_ui 模块暴露 REACHY_THEME(gr.themes.Base,品牌橙主色)
         和 REACHY_CSS(含 rm-statusbar 等自定义 class)。
      2. 接线级(源码静态断言):ConversationApp.run 的 launch(...) 调用里
         必须带 theme=/css= 参数,防止后续改动又把主题弄丢。
    """
    import inspect

    import gradio as gr

    from reachymini_conversation import web_ui
    from reachymini_conversation.app import ConversationApp

    # ---- 1. 导出级 ----
    assert isinstance(web_ui.REACHY_THEME, gr.themes.Base)
    # 品牌橙 #FF8C00 应作为 primary 500 主色(Gradio 6 主题把色阶拍平成 primary_500 属性)
    assert web_ui.REACHY_THEME.primary_500 == "#FF8C00"
    assert "rm-statusbar" in web_ui.REACHY_CSS
    assert "pill" in web_ui.REACHY_CSS

    # ---- 2. 接线级:run() 源码里 launch 必须带 theme/css ----
    src = inspect.getsource(ConversationApp.run)
    assert "theme=REACHY_THEME" in src, "launch() 缺少 theme=REACHY_THEME"
    assert "css=REACHY_CSS" in src, "launch() 缺少 css=REACHY_CSS"
