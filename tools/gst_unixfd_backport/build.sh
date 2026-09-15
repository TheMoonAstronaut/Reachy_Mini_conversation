#!/usr/bin/env bash
# build.sh — 把 gst-plugins-bad 1.24 的 unixfd 插件 backport 编译到 GStreamer 1.20
#
# 背景:Ubuntu 22.04 自带 GStreamer 1.20,而 unixfd(unixfdsink/unixfdsrc)
# 1.24 才进官方源码树。reachy_mini SDK 的本地 IPC 视频链路依赖它:
#   mujoco 渲染 → UDP:5005 → daemon media server → unixfdsink
#   → /tmp/reachymini_camera_socket → unixfdsrc(SDK 客户端)
#
# 本脚本:
#   1. 下载官方 1.24.13 tarball(gst-plugins-bad / gst-plugins-base)
#   2. 用本目录的 gstunixfd120compat.h 兼容层编译(只需系统 gstreamer dev 头文件)
#   3. 安装到 ~/.local/share/gstreamer-1.0/plugins/(GStreamer 默认扫描路径,免 sudo)
#   4. gst-inspect 验证
#
# 依赖:gcc pkg-config libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libglib2.0-dev
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(mktemp -d /tmp/gst-unixfd-build.XXXXXX)"
PLUGIN_DIR="${HOME}/.local/share/gstreamer-1.0/plugins"
BAD_VER="1.24.13"
BASE_VER="1.24.13"

echo "[build] 工作目录: $WORK_DIR"
cd "$WORK_DIR"

echo "[build] 下载 gst-plugins-bad-${BAD_VER} / gst-plugins-base-${BASE_VER} 源码..."
curl -sSL -o bad.tar.xz "https://gstreamer.freedesktop.org/src/gst-plugins-bad/gst-plugins-bad-${BAD_VER}.tar.xz"
curl -sSL -o base.tar.xz "https://gstreamer.freedesktop.org/src/gst-plugins-base/gst-plugins-base-${BASE_VER}.tar.xz"

tar -xf bad.tar.xz  "gst-plugins-bad-${BAD_VER}/gst/unixfd"
tar -xf base.tar.xz "gst-plugins-base-${BASE_VER}/gst-libs/gst/allocators/gstshmallocator.c" \
                    "gst-plugins-base-${BASE_VER}/gst-libs/gst/allocators/gstshmallocator.h"

SRC="gst-plugins-bad-${BAD_VER}/gst/unixfd"
cp "gst-plugins-base-${BASE_VER}/gst-libs/gst/allocators/gstshmallocator.c" \
   "gst-plugins-base-${BASE_VER}/gst-libs/gst/allocators/gstshmallocator.h" "$SRC/"
cp "$SCRIPT_DIR/gstunixfd120compat.h" "$SRC/"

echo "[build] 编译 libgstunixfd.so ..."
cd "$SRC"
gcc -shared -fPIC -O2 -D_GNU_SOURCE \
  -include gstunixfd120compat.h \
  -DPACKAGE='"gst-plugins-bad"' \
  -DGST_PACKAGE_NAME='"GStreamer Bad Plug-ins (unixfd backport)"' \
  -DGST_PACKAGE_ORIGIN='"https://gstreamer.freedesktop.org"' \
  -DVERSION="\"$(pkg-config --modversion gstreamer-1.0)\"" \
  $(pkg-config --cflags gstreamer-1.0 gstreamer-base-1.0 gstreamer-allocators-1.0 gio-2.0 gio-unix-2.0) \
  -o libgstunixfd.so gstunixfd.c gstunixfdsink.c gstunixfdsrc.c gstshmallocator.c \
  $(pkg-config --libs gstreamer-1.0 gstreamer-base-1.0 gstreamer-allocators-1.0 gio-2.0 gio-unix-2.0)

mkdir -p "$PLUGIN_DIR"
cp libgstunixfd.so "$PLUGIN_DIR/"

# 清 registry 缓存让新插件被发现
rm -f "${HOME}/.cache/gstreamer-1.0/registry."*.bin

echo "[build] 验证 ..."
gst-inspect-1.0 unixfdsink >/dev/null && echo "[build] unixfdsink OK"
gst-inspect-1.0 unixfdsrc  >/dev/null && echo "[build] unixfdsrc  OK"
echo "[build] 完成: $PLUGIN_DIR/libgstunixfd.so"
