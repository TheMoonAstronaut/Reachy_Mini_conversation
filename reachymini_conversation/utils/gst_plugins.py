"""utils.gst_plugins — sim 视频流链路的 GStreamer 插件存在性 guard(P7.B)。

用途:
    sim 视频流链路 = mujoco 渲染 → GStreamer UDP:5005 → daemon media server
    → unixfd IPC → SDK 客户端。任一环节缺插件,视频流都会静默不可用
    (历史上:webrtcsink 缺 → media server 整个起不来;unixfd 在 GStreamer 1.20
    上不存在 → 本地客户端收不到帧)。

    这个 guard 在启动前/排障时一次性列出缺失元素,配合
    docs/TROUBLESHOOTING.md 的"sim 视频流黑屏排查路径"使用。

注意:
    - webrtcsink 不在必需列表里:它只影响 WebRTC 远程流;本地 IPC 链路有
      P7.B 降级补丁(SDK media_server.py 打了"无 webrtcsink 仅本地 IPC"补丁)。
    - unixfdsink/unixfdsrc 在 GStreamer < 1.24 上来自本机 backport 插件
      (~/.local/share/gstreamer-1.0/plugins/libgstunixfd.so)。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# sim 视频流(本地 IPC 链路)必需的 GStreamer 元素
SIM_VIDEO_REQUIRED_ELEMENTS: tuple[str, ...] = (
    "appsrc",        # mujoco 渲染帧注入(UDP 发送侧)
    "udpsink",       # UDP:5005 发送
    "udpsrc",        # daemon media server 接收
    "rtpvrawdepay",  # RTP raw 解包
    "videoconvert",  # 格式转换
    "unixfdsink",    # daemon → 本地客户端 IPC(< gst 1.24 需 backport 插件)
    "unixfdsrc",     # 客户端 IPC 读帧
)


def missing_gst_elements(
    elements: tuple[str, ...] = SIM_VIDEO_REQUIRED_ELEMENTS,
) -> list[str]:
    """返回缺失的 GStreamer 元素名列表;空列表 = 全部可用。

    gi/Gst 本身不可用时,保守返回全部元素(视为不可用)并打 warning。
    """
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
    except Exception as e:  # noqa: BLE001 - guard 不能因自身异常弄挂调用方
        logger.warning(f"[gst-guard] gi/Gst 不可用,无法检查插件: {e}")
        return list(elements)

    return [name for name in elements if Gst.ElementFactory.make(name) is None]


def check_sim_video_plugins() -> bool:
    """便捷入口:检查 sim 视频流插件,缺失时打 warning,返回是否全部就绪。"""
    missing = missing_gst_elements()
    if missing:
        logger.warning(
            f"[gst-guard] sim 视频流缺少 GStreamer 元素: {missing} "
            "(排查路径见 docs/TROUBLESHOOTING.md)"
        )
        return False
    logger.info("[gst-guard] sim 视频流 GStreamer 插件齐全")
    return True
