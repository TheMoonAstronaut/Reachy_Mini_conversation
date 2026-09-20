#!/usr/bin/env bash
# =============================================================================
# start.sh — Linux 一键启动 Reachy Mini Conversation
# =============================================================================
# 用法:
#   ./scripts/start.sh                      默认 pure_sim + Web UI(7860)
#   ./scripts/start.sh --ui                 显式启动 Web UI(同上,等价)
#   ./scripts/start.sh --real               real_plus_sim(需要真机)
#   ./scripts/start.sh --no-media           降级:daemon 不带媒体(无相机/音频,
#                                           sim 视频流不可用,仅排障用)
#   ./scripts/start.sh --preload-datasets   预下载 HF emotions dataset(决策 16D)
#   ./scripts/start.sh --daemon-only        只启动 daemon,不启动应用
#
# 行为:
#   1. unset 6 个代理变量(否则 httpx 因 socks:// scheme 崩,见
#      docs/TROUBLESHOOTING.md)
#   2. 激活 conda reachy 环境
#   3. 校验依赖
#   4. 启动 daemon(后台,Mujoco 仿真,默认带媒体 + --headless):
#      `python -m reachymini_conversation.daemon_launcher --sim --headless`
#      - 眼睛流:mujoco eye_camera 渲染 → UDP:5005 → daemon media server
#        → unixfd IPC → SDK 客户端 → /sim_feed
#      - 场景流:mujoco studio_close 渲染 → UDP:5006 → app 侧接收 → /scene_feed
#      - --headless = 关闭原生 MuJoCo viewer 弹窗,但视频流保留
#        (由 daemon_launcher 的方案 B monkey-patch 实现,不改 site-packages)
#   5. 启动本应用(--ui → `python -m reachymini_conversation --ui`;
#      否则 legacy main.py CLI,带 deprecation 提示)
# =============================================================================
set -euo pipefail

# ---------- 代理变量清理(必须最先做) ----------
# 本机桌面环境可能残留 socks:// 代理变量;httpx 不支持 socks scheme,
# 会抛 ValueError: Unknown scheme for proxy URL URL('socks://...')。
# 6 个变量(大小写两套)必须全部清掉。
unset HTTPS_PROXY HTTP_PROXY ALL_PROXY https_proxy http_proxy all_proxy

# 路径
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[start]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[err]${NC} $*" >&2; }

# ---------- 参数解析 ----------
USE_REAL=false
USE_UI=false
PRELOAD_DATASETS=false
DAEMON_ONLY=false
NO_MEDIA=false
ROBOT_MODE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --real)              USE_REAL=true; shift ;;
        --ui)                USE_UI=true; shift ;;
        --preload-datasets)  PRELOAD_DATASETS=true; shift ;;
        --daemon-only)       DAEMON_ONLY=true; shift ;;
        --no-media)          NO_MEDIA=true; shift ;;
        --robot)             ROBOT_MODE=true; shift ;;
        -h|--help)
            echo "用法: $0 [--real] [--ui] [--preload-datasets] [--daemon-only] [--no-media] [--robot]"
            echo ""
            echo "  --real               真机 + 仿真镜像模式"
            echo "  --ui                 启动 Web UI(python -m reachymini_conversation --ui)"
            echo "  --preload-datasets   预下载 HF emotions dataset(play_emotion 工具)"
            echo "  --daemon-only        只启动 daemon,不启动应用"
            echo "  --no-media           降级模式:daemon 禁用全部媒体(相机/音频),"
            echo "                       sim 视频流不可用,仅媒体链路故障时排障用"
            echo "  --robot              on-robot 模式:跑在无线版机身树莓派上,"
            echo "                       不启动 sim daemon,直连本体官方 daemon(:8000),"
            echo "                       局域网内任意设备打开 UI 控制"
            echo "  -h, --help           显示帮助"
            exit 0
            ;;
        *) err "未知参数: $1"; exit 1 ;;
    esac
done

# ---------- 激活 Python 环境 ----------
# 优先 conda(开发机);无 conda 时回退已激活的 venv/系统 python(on-robot
# 树莓派部署用 ~/.venv/reachy,激活后直接进本脚本即可)
CONDA_ENV="${CONDA_ENV:-reachy}"

if command -v conda >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate "$CONDA_ENV"
    log "已激活 conda 环境:$CONDA_ENV"
else
    log "无 conda,使用当前 Python 环境($(command -v python || echo 未找到))"
fi

log "Python: $(python --version 2>&1)"
log "PWD:    $(pwd)"

# ---------- 校验 ----------
if ! python -c "import reachy_mini" 2>/dev/null; then
    err "reachy_mini 未安装。请先:pip install -e ."
    exit 1
fi

if ! python -c "import reachymini_conversation" 2>/dev/null && ! python -c "import reachy_mini" 2>/dev/null; then
    warn "reachymini_conversation 包未安装。尝试 pip install -e ."
    pip install -e . >/dev/null
fi

# ---------- 真机声卡音量恢复(易失设置,重启即丢,每次启动拉满) ----------
# 2026-09-16 实测:"声音非常偏小"根因是 ALSA 'PCM',0 输出主音量被砍到
# -33dB(45%)。Seeed wiki 建议:所有控件保持 100%(PCM 输出可按需调)。
# 注意:按名字探测 card 号(USB 重枚举后 card N 可能变化);只动
#       Reachy Mini Audio 卡,不碰板载声卡;未接真机时静默跳过。
# 另:pactl 默认输入源(板载活麦)也是运行时改动,重启机器后需重跑。
setup_reachy_audio() {
    local card
    card="$(aplay -l 2>/dev/null | sed -n 's/^card \([0-9]\+\):.*Reachy Mini Audio.*/\1/p' | head -1)"
    if [[ -z "$card" ]]; then
        return 0  # 未接真机(pure_sim),静默跳过
    fi
    local ctl
    for ctl in "PCM,0" "PCM,1" "Headset,0" "Headset,1"; do
        amixer -c "$card" sset "$ctl" 100% >/dev/null 2>&1 || true
    done
    log "Reachy Mini Audio(card $card)音量控件已设为 100%(易失设置,每次启动恢复)"
}
setup_reachy_audio

# ---------- 启动 daemon ----------
# P7.B:媒体链路已修好(unixfd backport 插件 + SDK media_server 降级补丁),
# 默认带媒体启动;--no-media 仅作降级排障用。
# 场景流(网页主区 studio_close 第三人称):
#   daemon 改由 reachymini_conversation.daemon_launcher 启动 —— 它在调 SDK
#   daemon main() 前对 MujocoBackend 做运行时 monkey-patch(不改 site-packages):
#   1) 修 rendering_loop 的 dest_port 忽略 bug(SDK 1.10.0 永远发 5005);
#   2) 额外起 studio_close→UDP:5006 渲染线程;
#   3) --headless 时补回 eye_camera→5005 渲染线程(SDK 把"弹窗"和"推流"
#      绑在同一个 `if not headless` 里,patch 把两者解耦)。
# 因此 --headless 只表示"无原生 MuJoCo 弹窗",视频流(5005/5006)都保留。
# --robot(on-robot)模式:跳过 sim daemon,直连机器人本体官方 daemon(:8000)。
if [[ "$ROBOT_MODE" == "true" ]]; then
    export REACHYMINI_RUN_MODE=pure_real
    log "on-robot 模式:检查机器人本体 daemon(:8000,官方系统服务)…"
    if ! curl -sf --max-time 5 http://127.0.0.1:8000/api/daemon/status > /dev/null 2>&1; then
        err "本体 daemon 未就绪。请在树莓派上确认官方 daemon 服务在运行:"
        err "  systemctl status reachy-mini-daemon(或按官方文档排查)"
        exit 1
    fi
    log "本体 daemon 已就绪(不启动 sim daemon)"
    DAEMON_PID=""
else
    DAEMON_FLAGS="--sim --headless"
    if [[ "$NO_MEDIA" == "true" ]]; then
        DAEMON_FLAGS="$DAEMON_FLAGS --no-media"
        warn "--no-media:daemon 禁用相机/音频,sim 视频流将不可用(UI 显示占位图)"
    fi
    if [[ "$PRELOAD_DATASETS" == "true" ]]; then
        DAEMON_FLAGS="$DAEMON_FLAGS --preload-datasets"
        warn "决策 16D:首次启动会从 HF 下载 emotions dataset(~100MB)"
    fi

    log "启动 daemon(launcher 方案 B):python -m reachymini_conversation.daemon_launcher $DAEMON_FLAGS"
    # 守护进程后台跑,日志到 /tmp/reachy-daemon.log
    python -m reachymini_conversation.daemon_launcher $DAEMON_FLAGS > /tmp/reachy-daemon.log 2>&1 &
    DAEMON_PID=$!
    log "Daemon PID: $DAEMON_PID,日志:tail -f /tmp/reachy-daemon.log"

    # 等 daemon 起好:轮询 HTTP API 直到就绪(固定 sleep 3 不够 ——
    # daemon 的 lifespan 要完成 mujoco 加载 + wake_up 才接受连接,
    # 约 10~20s;app 过早连接会 fallback 到 reachy-mini.local 然后崩)
    log "等待 daemon 就绪(轮询 http://127.0.0.1:8000/api/daemon/status)..."
    DAEMON_READY=false
    for _ in $(seq 1 60); do
        if curl -sf -m 2 "http://127.0.0.1:8000/api/daemon/status" >/dev/null 2>&1; then
            DAEMON_READY=true
            break
        fi
        # daemon 进程挂了就不用等了
        if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
            err "daemon 进程提前退出,日志见 /tmp/reachy-daemon.log"
            exit 1
        fi
        sleep 1
    done
    if [[ "$DAEMON_READY" != "true" ]]; then
        err "等待 daemon 就绪超时(60s),日志见 /tmp/reachy-daemon.log"
        exit 1
    fi
    log "daemon 已就绪"

    cleanup() {
        log "关闭 daemon (PID $DAEMON_PID)..."
        kill "$DAEMON_PID" 2>/dev/null || true
        wait "$DAEMON_PID" 2>/dev/null || true
    }
    trap cleanup EXIT INT TERM

    if [[ "$DAEMON_ONLY" == "true" ]]; then
        log "仅 daemon 模式已启动。Ctrl+C 退出。"
        wait "$DAEMON_PID"
        exit 0
    fi
fi

# ---------- 启动应用(UI 为唯一入口;legacy CLI 已随开源清理移除)----------
log "启动 Reachy Mini Conversation..."
echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  Reachy Mini Conversation 已启动${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""

# 局域网访问提示(尽早输出,不等 python 侧 banner):同 WiFi 设备浏览器输入
# 该链接即可。注意必须带 http:// 前缀 —— 部分手机浏览器输 IP 会默认
# 升级 https,而本服务是 http,会报"连接不安全"(见 docs/TROUBLESHOOTING.md)。
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [[ -n "${LAN_IP}" && "${LAN_IP}" != "127.0.0.1" ]]; then
    echo -e "${GREEN}[start]${NC} 本机访问:   http://localhost:7860"
    echo -e "${GREEN}[start]${NC} 局域网访问: ${YELLOW}http://${LAN_IP}:7860${NC}  (同 WiFi 设备可打开,注意带 http:// 前缀)"
    echo ""
fi

log "UI 模式(python -m reachymini_conversation --ui)"
python -m reachymini_conversation --ui
