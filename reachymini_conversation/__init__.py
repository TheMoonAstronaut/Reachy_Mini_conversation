"""reachymini_conversation — Reachy Mini 对话应用包。"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)


def _win_preload_libexpat() -> None:
    """Windows:抢先加载 conda 的 libexpat.dll,绕开 gstreamer-bundle 冲突。

    背景(2026-09-28 Windows 11 实测):gstreamer-bundle 自带的
    ``gstreamer_libs\\bin\\libexpat.dll`` 与 conda Python 的 ``pyexpat.pyd``
    不兼容。import reachy_mini 初始化 GStreamer 会先把坏副本载入进程,
    之后 mediapipe/matplotlib(经 plistlib)导入 pyexpat 必炸:
    ``DLL load failed while importing pyexpat``。

    这里在包导入的最早期(早于任何 reachy_mini / Gst 初始化)把环境里
    conda 自带的兼容副本钉进进程 —— 之后 pyexpat 与 GStreamer 插件都
    绑定这份可用的 libexpat。对应用/测试全透明,免手动换 DLL 文件。
    (若先加载了 gstreamer 那份坏副本,此函数无济于事;那属于绕过了本
    包直接 import reachy_mini 的场景,README 另附手动修复。)
    """
    if sys.platform != "win32":
        return
    candidates = [
        os.path.join(sys.prefix, "Library", "bin", "libexpat.dll"),
        os.path.join(sys.base_prefix, "Library", "bin", "libexpat.dll"),
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                import ctypes

                ctypes.WinDLL(path)
                logger.debug(f"[win-fixup] 已预加载 libexpat: {path}")
                return
            except OSError as e:
                logger.debug(f"[win-fixup] 预加载 {path} 失败: {e}")


_win_preload_libexpat()
