#!/usr/bin/env bash
# =============================================================================
# install_deps.sh — 一键安装 Linux 系统依赖
# =============================================================================
# 仅装 OS 层库(.deb),conda/pip 依赖走 environment.yml。
# 需要 sudo / root 权限。
#
# 用法:sudo ./scripts/install_deps.sh
# =============================================================================
set -euo pipefail

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[deps]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[err]${NC} $*" >&2; }

# 检查 root
if [[ $EUID -ne 0 ]]; then
    err "需要 root 权限,请用 sudo 执行:sudo $0"
    exit 1
fi

# 检测发行版
if ! command -v apt-get >/dev/null 2>&1; then
    err "仅支持 Debian/Ubuntu。其他发行版请手动安装等价包。"
    exit 1
fi

log "更新包索引..."
apt-get update

log "安装系统依赖..."
# Cairo / GObject —— PyGObject 编译需要
# GStreamer —— Mujoco 视频流 + 音频
# pkg-config —— 编译期工具
apt-get install -y --no-install-recommends \
    ca-certificates \
    libcairo2-dev \
    libgirepository1.0-dev \
    pkg-config \
    python3-dev \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-libav \
    libportaudio2 \
    libasound2-dev \
    ffmpeg

log "清理 apt 缓存..."
apt-get clean
rm -rf /var/lib/apt/lists/*

log "验证 GStreamer 安装..."
if command -v gst-inspect-1.0 >/dev/null 2>&1; then
    gst_version=$(gst-inspect-1.0 --version | head -n1)
    log "GStreamer: $gst_version"
else
    warn "gst-inspect-1.0 未找到,可能 GStreamer 没装好"
fi

log "完成 ✅"
echo ""
echo "下一步:"
echo "  1. conda env create -f environment.yml"
echo "  2. conda activate reachy"
echo "  3. pip install -e ."
echo "  4. ./scripts/start.sh"
