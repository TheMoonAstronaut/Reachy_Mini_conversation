"""P7.B gst_plugins guard 单测。

用途:sim 视频流黑屏的历史根因是 GStreamer 插件缺失(webrtcsink / unixfd 等),
`reachymini_conversation.utils.gst_plugins` 提供存在性检查。本测试用 mock 验证:

1. 全部元素可创建 → 返回空缺失列表 / True
2. 部分元素缺失 → 返回准确缺失列表 / False
3. gi 不可用时 → 保守返回全部元素,不抛异常

不依赖真实 GStreamer(mock Gst.ElementFactory.make),保证在无显示器/无插件的
CI 环境也能跑。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from reachymini_conversation.utils.gst_plugins import (  # noqa: E402
    SIM_VIDEO_REQUIRED_ELEMENTS,
    check_sim_video_plugins,
    missing_gst_elements,
)


def _make_fake_gi(available: set[str]):
    """构造假 gi 模块:Gst.ElementFactory.make 只对 available 里的元素返回对象。"""
    fake_gst = types.SimpleNamespace()
    fake_gst.init = lambda *_a, **_kw: None
    fake_factory = types.SimpleNamespace(
        make=lambda name: object() if name in available else None
    )
    fake_gst.ElementFactory = fake_factory

    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *_a, **_kw: None
    fake_gi.repository = types.SimpleNamespace(Gst=fake_gst)
    return fake_gi


def test_all_plugins_present():
    """全部插件可用 → 无缺失,check 返回 True。"""
    fake_gi = _make_fake_gi(set(SIM_VIDEO_REQUIRED_ELEMENTS))
    with mock.patch.dict(sys.modules, {"gi": fake_gi, "gi.repository": fake_gi.repository}):
        assert missing_gst_elements() == []
        assert check_sim_video_plugins() is True


def test_some_plugins_missing():
    """缺 unixfd 两个元素(历史真机根因)→ 准确报告,check 返回 False。"""
    available = set(SIM_VIDEO_REQUIRED_ELEMENTS) - {"unixfdsink", "unixfdsrc"}
    fake_gi = _make_fake_gi(available)
    with mock.patch.dict(sys.modules, {"gi": fake_gi, "gi.repository": fake_gi.repository}):
        assert missing_gst_elements() == ["unixfdsink", "unixfdsrc"]
        assert check_sim_video_plugins() is False


def test_gi_unavailable():
    """gi 导入失败 → 保守返回全部元素视为缺失,且不抛异常。"""
    with mock.patch.dict(sys.modules, {"gi": None}):
        assert missing_gst_elements() == list(SIM_VIDEO_REQUIRED_ELEMENTS)
        assert check_sim_video_plugins() is False


def test_custom_element_list():
    """支持自定义元素列表(例如检查 webrtcsink 是否存在)。"""
    fake_gi = _make_fake_gi({"webrtcsink"})
    with mock.patch.dict(sys.modules, {"gi": fake_gi, "gi.repository": fake_gi.repository}):
        assert missing_gst_elements(("webrtcsink",)) == []
        assert missing_gst_elements(("webrtcsink", "nonexistent_xyz")) == ["nonexistent_xyz"]
