"""reachymini_conversation.local_audio — 有线真机的本机 USB 声卡直连(V2 Fix C)。

为什么不走 daemon B 的媒体链:
  daemon B 以 --no-media 运行(避免与 sim daemon 抢 unixfd socket / UDP 5005 /
  WebRTC 8443)。但有线版真机的麦克风/扬声器就是**本机的 USB 声卡**
  (Pollen Robotics Reachy Mini Audio),app 进程可以直接开 ——
  SDK 的 `GStreamerAudio` 就是干这个的(与 daemon 无关,直接操作声卡)。

用法(懒单例,首次调用时初始化;无声卡时返回 None,调用方降级):
    audio = get_local_audio()
    if audio: audio.start_recording(); chunk = audio.get_audio_sample()

设备识别:SDK 的 get_audio_device("Source"/"Sink") 内部用 Gst.DeviceMonitor
按名字匹配 "Reachy Mini Audio"(见 SDK media/device_detection.py)。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_AUDIO: Any | None = None
_LOCAL_AUDIO_FAILED = False


def get_local_audio() -> Any | None:
    """懒创建 GStreamerAudio 单例;初始化失败(无声卡)返回 None 且记住不再重试。"""
    global _LOCAL_AUDIO, _LOCAL_AUDIO_FAILED
    if _LOCAL_AUDIO is not None:
        return _LOCAL_AUDIO
    if _LOCAL_AUDIO_FAILED:
        return None
    try:
        from reachy_mini.media.audio_gstreamer import GStreamerAudio

        _LOCAL_AUDIO = GStreamerAudio(log_level="WARNING")
        logger.info("[local-audio] GStreamerAudio 初始化 OK(USB 声卡直连)")
        return _LOCAL_AUDIO
    except Exception as e:
        logger.warning(f"[local-audio] 初始化失败(无声卡?): {type(e).__name__}: {e}")
        _LOCAL_AUDIO_FAILED = True
        return None


def reset_local_audio() -> None:
    """释放(测试/重启用)。"""
    global _LOCAL_AUDIO, _LOCAL_AUDIO_FAILED
    if _LOCAL_AUDIO is not None:
        try:
            _LOCAL_AUDIO.cleanup()
        except Exception:
            pass
    _LOCAL_AUDIO = None
    _LOCAL_AUDIO_FAILED = False
