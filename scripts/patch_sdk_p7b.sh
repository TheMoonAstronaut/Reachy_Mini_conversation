#!/usr/bin/env bash
# =============================================================================
# patch_sdk_p7b.sh — 给 reachy-mini SDK 的 media_server.py 打 P7.B 降级补丁
# =============================================================================
# 背景:
#   daemon 的媒体服务器依赖 GStreamer webrtcsink(gst-plugins-rs)。
#   Ubuntu 22.04 官方仓库没有该插件(>= 23.10 才有 gstreamer1.0-plugins-rs),
#   原版 SDK 在 webrtcsink 缺失时直接 raise,导致 daemon 媒体服务器整体
#   初始化失败 → 本地 IPC(unixfdsink 眼睛流)也没有 → app 回退 WebRTC
#   连接被拒(ConnectionRefusedError),整个应用起不来。
#
#   本补丁把 "webrtcsink 缺失" 从致命错误降级为 "跳过 WebRTC 分支、
#   仅保留本地 IPC 分支"(视频/音频各一处 + _configure_webrtc 一处),
#   与 Windows 原生路径(win32ipcvideosink)行为对齐。
#
# 何时运行:
#   - conda env create -f environment.yml 重建环境之后
#   - pip 升级/重装 reachy-mini SDK 之后
#   脚本幂等:已打过的环境重复运行直接跳过。
#
# 用法:
#   conda activate reachy
#   ./scripts/patch_sdk_p7b.sh
# =============================================================================
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"

"$PYTHON_BIN" - <<'PYEOF'
import re
import sys
from pathlib import Path

import reachy_mini.media.media_server as ms

target = Path(ms.__file__)
src = target.read_text(encoding="utf-8")

# 已打补丁判定:_configure_webrtc 在 webrtcsink 缺失时 return None(而非 raise)
# (补丁注释里的 "P7.B degradation patch" 是完整连续的源文本,适合当标记)
if "P7.B degradation patch" in src:
    print(f"[patch-p7b] 已打过补丁,跳过:{target}")
    sys.exit(0)

original = src

# --- 改动 1: _configure_webrtc 缺失时降级而非 raise,返回 None ---
old1 = '''    def _configure_webrtc(self, pipeline: Gst.Pipeline) -> Gst.Element:
        self._logger.debug("Configuring WebRTC")
        webrtcsink = Gst.ElementFactory.make("webrtcsink")
        if not webrtcsink:
            raise RuntimeError(
                "Failed to create webrtcsink element. "
                "Is the GStreamer webrtc rust plugin installed?"
            )'''
new1 = '''    def _configure_webrtc(self, pipeline: Gst.Pipeline) -> Gst.Element | None:
        self._logger.debug("Configuring WebRTC")
        webrtcsink = Gst.ElementFactory.make("webrtcsink")
        if not webrtcsink:
            # P7.B degradation patch: no gst-plugins-rs on this host
            # (e.g. Ubuntu 22.04). Skip the WebRTC branch entirely and run
            # local-IPC only - the unixfdsink branch built in _configure_video
            # is all the local app needs. Remote WebRTC streaming is simply
            # unavailable in this mode.
            self._logger.warning(
                "webrtcsink element not available "
                "(gst-plugins-rs missing). WebRTC streaming disabled; "
                "local IPC branch only."
            )
            return None'''

# --- 改动 2: _configure_video 的 webrtcsink 形允许 None,且 WebRTC 分支可跳过 ---
old2 = '''    def _configure_video(
        self, cam_path: str, pipeline: Gst.Pipeline, webrtcsink: Gst.Element
    ) -> None:'''
new2 = '''    def _configure_video(
        self, cam_path: str, pipeline: Gst.Pipeline, webrtcsink: Gst.Element | None
    ) -> None:'''

old3 = '''        # WebRTC branch
        queue_webrtc = Gst.ElementFactory.make("queue", "queue_webrtc")'''
new3 = '''        # WebRTC branch (P7.B: skipped entirely when webrtcsink is unavailable)
        if webrtcsink is None:
            self._logger.info(
                "No webrtcsink: video pipeline runs IPC branch only."
            )
            return

        queue_webrtc = Gst.ElementFactory.make("queue", "queue_webrtc")'''

# --- 改动 3: _configure_audio 在 webrtcsink 为 None 时整体跳过 ---
old4 = '''    def _configure_audio(self, pipeline: Gst.Pipeline, webrtcsink: Gst.Element) -> None:'''
new4 = '''    def _configure_audio(
        self, pipeline: Gst.Pipeline, webrtcsink: Gst.Element | None
    ) -> None:'''

old5 = '''        self._logger.debug("Configuring audio")

        audiosrc = self._build_audio_source()'''
new5 = '''        self._logger.debug("Configuring audio")

        # P7.B: WebRTC streaming unavailable (webrtcsink missing) - audio over
        # WebRTC makes no sense; skip without tearing anything down.
        if webrtcsink is None:
            self._logger.info(
                "No webrtcsink: audio streaming (WebRTC) disabled."
            )
            return

        audiosrc = self._build_audio_source()'''

replacements = [
    (old1, new1, "_configure_webrtc 降级"),
    (old2, new2, "_configure_video 形参"),
    (old3, new3, "_configure_video 跳过分支"),
    (old4, new4, "_configure_audio 形参"),
    (old5, new5, "_configure_audio 跳过"),
]

applied = []
for old, new, name in replacements:
    if old in src:
        src = src.replace(old, new, 1)
        applied.append(name)

if src == original:
    print("[patch-p7b] 未找到任何已知的 SDK 代码模式,SDK 版本可能已变化,")
    print("[patch-p7b] 请人工核对 media_server.py 的 webrtcsink 处理逻辑。")
    sys.exit(1)

if len(applied) < len(replacements):
    missing = [n for (o, _, n) in replacements if not any(n == a for a in applied)]
    print(f"[patch-p7b] 警告:部分改动未匹配(可能已被其他版本修改):{missing}")

target.write_text(src, encoding="utf-8")
compile(src, str(target), "exec")
print(f"[patch-p7b] 补丁已应用({len(applied)}/{len(replacements)} 处):{target}")
PYEOF

echo "[patch-p7b] 完成 ✅"
