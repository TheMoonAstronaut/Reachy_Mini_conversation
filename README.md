# Reachy Mini Conversation

[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](environment.yml)
[![reachy_mini](https://img.shields.io/badge/reachy__mini-1.10.0-green.svg)](https://github.com/pollen-robotics/reachy_mini)

> **对话式机器人应用 / Conversational robot app**
>
>  基于 [Reachy Mini](https://www.pollen-robotics.com/reachy-mini/) SDK,使用**豆包大模型 (Doubao)** + **豆包流式语音识别 (WebSocket ASR)** + **Edge TTS** 提供完整的语音对话体验。
>
> Web UI 基于 **Gradio**,支持 Mujoco 仿真与真机镜像、声源定位驱动头部、手部跟随。

[English](#english) | [中文](#中文)

---

## 中文

### 简介

Reachy Mini Conversation 是一个开源的对话机器人应用,为 Pollen Robotics 的 Reachy Mini 机器人设计。它把豆包大语言模型(豆包方舟 Ark)、豆包流式语音识别 WebSocket API、Edge TTS 三者串起来,通过 Reachy Mini SDK 实现:

- 🎙️ **语音对话**:说中文 → 豆包 ASR 实时识别 → 豆包 LLM 回复 → Edge TTS 播音 → 机器人动
- 🦾 **身体动作**:LLM 可以主动调工具(dance / move_head / play_emotion 等)
- 👂 **声源定位**:麦克风阵列检测说话人方向,自动转头
- 👋 **手部跟随**(P6):摄像头检测手掌,头部跟随
- 🪞 **真机↔仿真镜像**:左侧 Mujoco 仿真 + 右侧真机/摄像头实时同步
- 🌐 **Gradio Web UI**:浏览器打开 `localhost:7860` 即可使用

**适用硬件**:Reachy Mini(USB 接线版 / 无线 RPi 版均兼容,本应用运行于用户 PC,无线版 RPi 只跑 daemon)。

### 效果展示

> 🚧 占位 / Placeholder — 待 P1+ 接入 Web UI 后补截图 / GIF

<!-- TODO(P1): 加 Gradio UI 截图 / 真机演示 GIF -->
<!-- ![Demo](docs/assets/demo.gif) -->

### 系统架构(简版)

```
┌──────────────────────────────────────┐
│  Web UI (Gradio @ localhost:7860)    │
│  ┌────────────┐  ┌────────────────┐  │
│  │ Mujoco 视频 │  │ 对话 / 配置面板 │  │
│  └────────────┘  └────────────────┘  │
└──────────────────────────────────────┘
                ↕
┌──────────────────────────────────────┐
│  App 进程                             │
│  MirrorOrchestrator │ VoicePipeline   │
│  ├ sim + real 镜像   │ ASR→LLM→TTS    │
│  SoundLocalizer │ HandFollower(P6)    │
└──────────────────────────────────────┘
                ↕
┌──────────────────────────────────────┐
│  reachy_mini SDK (1.10.0)             │
│  ReachyMini │ AudioDoA │ look_at_image│
└──────────────────────────────────────┘
                ↕
┌──────────────────────────────────────┐
│  硬件 / 仿真                         │
│  reachy-mini-daemon --sim / 真机      │
└──────────────────────────────────────┘
```

详细架构见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),详细安装见 [`docs/INSTALL.md`](docs/INSTALL.md)。

### 一键安装(Linux)

**前置**:Ubuntu / Debian 系统,Python 3.11+,sudo 权限。

```bash
# 1. 克隆仓库(目录名跟仓库一致,不要改)
git clone https://github.com/<your-org>/Reachy_Mini_conversation.git
cd Reachy_Mini_conversation

# 2. 安装系统依赖(cairo / gstreamer / ffmpeg)
sudo ./scripts/install_deps.sh

# 3. 创建并激活 conda 环境(推荐)
conda env create -f environment.yml
conda activate reachy

# 4. 安装 Python 包 + 注册 CLI
pip install -e ".[dev]"

# 5. (可选)首次启动会下载 ~230MB 情感数据集(决策 16D)
#    普通对话不需要,只有调 play_emotion 工具才用
```

### 一键启动

```bash
# 默认仿真模式
./scripts/start.sh

# 或:
reachy-mini-conversation
```

启动后浏览器打开 [http://localhost:7860](http://localhost:7860)。

### 配置 API Key

把 API Key 写到 `~/.reachymini/env.json`(用户级,不入 git):

```bash
mkdir -p ~/.reachymini
cat > ~/.reachymini/env.json <<'EOF'
{
  "doubao_llm": {
    "api_key": "your-ark-api-key",
    "model": "doubao-seed-character-251128"
  },
  "doubao_asr": {
    "api_key": "your-asr-api-key"
  },
  "edge_tts": {
    "voice": "zh-CN-XiaoxiaoNeural"
  },
  "run_mode": "pure_sim"
}
EOF
chmod 600 ~/.reachymini/env.json
```

申请地址:
- **豆包 LLM**:[火山引擎方舟控制台](https://www.volcengine.com/docs/6561/1354869)
- **豆包 ASR**:[火山引擎语音技术](https://www.volcengine.com/product/asr)

详细配置见 [`docs/CONFIG.md`](docs/CONFIG.md)。

### 进阶功能

| 功能 | 阶段 | 说明 |
|---|---|---|
| Web 端 + Mujoco 仿真镜像 | P1-P2 | 默认 sim,可切真机 |
| 豆包 ASR 接入对话管线 | P4 | WebSocket 流式,服务端 VAD |
| 声源定位头部转向 | P5 | AudioDoA 后台线程 |
| 手部跟随(MediaPipe) | P6 | mediapipe 1.0+ tasks API |
| LLM 工具完整化 | P7 | dance / move_head / play_emotion 等 |
| Windows 适配 + CI | P8 | 仅占位 |

### 故障排查

遇到问题先查 [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)。

常见问题:
- **找不到 conda**:安装 [Miniconda](https://docs.conda.io/en/latest/miniconda.html)
- **Mujoco 视频流端口被占**:`lsof -i :5005` 找占用进程杀掉
- **豆包 ASR 连接失败**:检查 API Key 和网络(白名单 `openspeech.bytedance.com`)

### 贡献 & 许可证

- 许可证:[Apache 2.0](LICENSE)(与 [reachy_mini](https://github.com/pollen-robotics/reachy_mini) 一致)
- 决策记录:[`agents.local.md`](agents.local.md) + [`plan.md`](plan.md)
- 任务清单:[`P0_TASKS.md`](P0_TASKS.md)
- 当前阶段:**P0(基础设施)**

---

## English

### Overview

Reachy Mini Conversation is an open-source conversational robot app for the Pollen Robotics Reachy Mini. It wires together **Doubao LLM (Ark)**, **Doubao streaming ASR (WebSocket)**, and **Edge TTS** through the Reachy Mini SDK to deliver:

- 🎙 **Voice conversation**: speak Chinese → Doubao ASR streams text → Doubao LLM replies → Edge TTS plays → robot moves
- 🦾 **Body motion**: LLM can call tools (dance / move_head / play_emotion, …)
- 👂 **Sound source localization**: microphone array locates speaker, robot turns its head
- 👋 **Hand following** (P6): camera detects palm, head follows
- 🪞 **Real ↔ sim mirroring**: Mujoco sim + real robot/ camera side by side
- 🌐 **Gradio Web UI**: open `localhost:7860` in a browser

**Supported hardware**: Reachy Mini (USB wired **and** wireless RPi variants; the app runs on the user's PC, the wireless RPi only runs the daemon).

### Demo

> 🚧 Placeholder — screenshots / GIF will come after P1+

<!-- TODO(P1): add Gradio UI screenshot / hardware demo GIF -->
<!-- ![Demo](docs/assets/demo.gif) -->

### Architecture (simplified)

```
┌──────────────────────────────────────┐
│  Web UI (Gradio @ localhost:7860)    │
│  ┌────────────┐  ┌────────────────┐  │
│  │ Mujoco feed│  │ Chat / config  │  │
│  └────────────┘  └────────────────┘  │
└──────────────────────────────────────┘
                ↕
┌──────────────────────────────────────┐
│  App process                          │
│  MirrorOrchestrator │ VoicePipeline   │
│  ├ sim + real mirror │ ASR→LLM→TTS    │
│  SoundLocalizer │ HandFollower (P6)   │
└──────────────────────────────────────┘
                ↕
┌──────────────────────────────────────┐
│  reachy_mini SDK (1.10.0)             │
│  ReachyMini │ AudioDoA │ look_at_image│
└──────────────────────────────────────┘
                ↕
┌──────────────────────────────────────┐
│  Hardware / simulation               │
│  reachy-mini-daemon --sim / hardware  │
└──────────────────────────────────────┘
```

Full architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), full install: [`docs/INSTALL.md`](docs/INSTALL.md).

### One-click install (Linux)

**Prerequisites**: Ubuntu / Debian, Python 3.11+, sudo.

```bash
# 1. Clone (keep the directory name)
git clone https://github.com/<your-org>/Reachy_Mini_conversation.git
cd Reachy_Mini_conversation

# 2. Install system dependencies (cairo / gstreamer / ffmpeg)
sudo ./scripts/install_deps.sh

# 3. Create + activate conda env (recommended)
conda env create -f environment.yml
conda activate reachy

# 4. Install Python packages + register CLI
pip install -e ".[dev]"

# 5. (Optional) On first run with `play_emotion`, ~230 MB of HF dataset is downloaded
```

### One-click run

```bash
# Default: sim mode
./scripts/start.sh

# Or:
reachy-mini-conversation
```

Then open [http://localhost:7860](http://localhost:7860).

### Configure API keys

Write API keys to `~/.reachymini/env.json` (user-level, never committed):

```bash
mkdir -p ~/.reachymini
cat > ~/.reachymini/env.json <<'EOF'
{
  "doubao_llm": {
    "api_key": "your-ark-api-key",
    "model": "doubao-seed-character-251128"
  },
  "doubao_asr": {
    "api_key": "your-asr-api-key"
  },
  "edge_tts": {
    "voice": "zh-CN-XiaoxiaoNeural"
  },
  "run_mode": "pure_sim"
}
EOF
chmod 600 ~/.reachymini/env.json
```

Where to apply:
- **Doubao LLM**: [Volcano Engine Ark console](https://www.volcengine.com/docs/6561/1354869)
- **Doubao ASR**: [Volcano Engine ASR](https://www.volcengine.com/product/asr)

See [`docs/CONFIG.md`](docs/CONFIG.md) for all options.

### Advanced features (roadmap)

| Feature | Phase | Note |
|---|---|---|
| Web UI + Mujoco mirror | P1-P2 | Default sim, switchable to real |
| Doubao ASR voice pipeline | P4 | WebSocket streaming, server-side VAD |
| Sound source localization | P5 | AudioDoA background thread |
| Hand following (MediaPipe) | P6 | mediapipe 1.0+ tasks API |
| LLM tool calls | P7 | dance / move_head / play_emotion |
| Windows + CI | P8 | placeholder only |

### Troubleshooting

See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

### Contributing / License

- License: [Apache 2.0](LICENSE)(same as [reachy_mini](https://github.com/pollen-robotics/reachy_mini))
- Decisions: [`agents.local.md`](agents.local.md) + [`plan.md`](plan.md)
- Tasks: [`P0_TASKS.md`](P0_TASKS.md)
- Current phase: **P0 (infrastructure)**

---

## Status

| Phase | Status | Note |
|---|---|---|
| P0 | ✅ | Infrastructure + cleanup |
| P1 | ✅ | App base + Gradio Blocks |
| P2 | ✅ | Mode switch + Mujoco feed + mirroring |
| P3 | ✅ | Chat panel + API key config |
| P4 | ✅ | Doubao ASR voice pipeline |
| P5 | ✅ | Sound localization |
| P6 | ✅ | Hand following |
| P7 | ✅ | Full LLM tool calls (real function calling) |
| P8 | 🟡 | Windows + CI (lint strict for new code, legacy exempted) |

See [`plan.md`](plan.md) for the full roadmap.

---

## Related projects

- [reachy_mini SDK](https://github.com/pollen-robotics/reachy_mini) — Pollen Robotics
- [reachy_mini_conversation_app](https://github.com/pollen-robotics/reachy_mini_conversation_app) — official HF-Realtime reference
- [reachy_mini_dances_library](https://github.com/pollen-robotics/reachy_mini_dances_library) — dance moves
- [speech-to-speech](https://github.com/huggingface/speech-to-speech) — HF realtime backend
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) — considered but not used (no torch needed, but we kept cloud ASR)