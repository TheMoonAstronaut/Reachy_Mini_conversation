# INSTALL.md — 安装指南 / Installation Guide

> 适用版本:v1.0.0
> 快速上手先看根目录 [README.md](../README.md),本文是完整版安装细节。

---

## 中文

### 系统要求

| 项 | 最低 | 推荐 |
|---|---|---|
| OS | Ubuntu 22.04 / Debian 11 | Ubuntu 24.04 |
| Python | 3.11 | 3.12(environment.yml 锁 3.12) |
| conda | Miniconda 或 Anaconda | Miniconda |
| 磁盘 | 5 GB | 10 GB |
| 内存 | 4 GB | 8 GB |
| GPU | 不需要 | 不需要(全部 CPU 推理) |
| 硬件 | 无(纯仿真即可跑) | Reachy Mini(USB 有线 / 无线 RPi) |

### Linux(Ubuntu / Debian)完整安装

#### 步骤 1:克隆仓库

```bash
git clone https://github.com/TheMoonAstronaut/Reachy_Mini_conversation.git
cd Reachy_Mini_conversation
```

> ⚠️ **目录名不要改**:多个脚本默认仓库根目录叫 `Reachy_Mini_conversation`。

#### 步骤 2:装系统依赖

```bash
sudo ./scripts/install_deps.sh
```

这一步用 apt 安装:
- `cairo` / `gobject-introspection` / `pkg-config` — PyGObject 编译需要
- `gstreamer1.0` + 插件 — Mujoco 视频流 + 音频采集/播放
- `portaudio` / `alsa` — 音频录制
- `ffmpeg` — TTS 响度归一化

装完验证:`gst-inspect-1.0 --version` 和 `ffmpeg -version` 都能找到即可。

#### 步骤 3:装 conda(如果没有)

```bash
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh
# 重启 shell 让 conda init 生效
```

#### 步骤 4:建 conda 环境 + 装 Python 包

```bash
conda env create -f environment.yml
conda activate reachy
pip install -e ".[dev]"
```

主要依赖:
- `reachy_mini >= 1.10.0` — 官方 SDK
- `reachy_mini_dances_library` — 舞蹈动作库
- `numpy >= 2.2.5` / `scipy` / `soundfile` — 数值 / 音频
- `gradio` / `fastapi` / `uvicorn` — Web UI
- `edge-tts` — TTS(合成层,重试 2 次)
- `httpx` / `websockets` — 豆包 LLM / ASR 网络
- `mediapipe` — 手部跟随(传递依赖含 opencv)
- `av` — 视频/音频解码
- `[dev]` extras:`pytest` / `black` / `ruff` / `mypy`

**注意**:不装 torch / onnxruntime-gpu(全部 CPU,无线版 RPi 也兼容);不装本地 ASR(语音识别走云端豆包流式 API)。

#### 步骤 5:配置 API Key(语音对话必需)

见 [README.md](../README.md#配置-api-key) 或 [`CONFIG.md`](CONFIG.md)。纯仿真看看界面可以先跳过,但发消息会报 LLM 错误。

#### 步骤 6:(可选)下载手部跟随模型

```bash
mkdir -p ~/.cache/reachymini
curl -L -o ~/.cache/reachymini/hand_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task
```

约 8 MB。不下也能跑,只是「手部跟随」功能不可用(顶部徽章会显示不可用)。

#### 步骤 7:验证安装

```bash
reachy-mini-conversation --help
pytest tests/smoke_test.py -v
```

全部 pass 即安装成功。

#### 步骤 8:启动

```bash
./scripts/start.sh --ui
```

启动内容:
1. 启动 Mujoco 仿真 daemon(`reachy-mini-daemon --sim --headless`,媒体走 UDP 5005/5006)
2. 启动应用本体(Gradio @ 7860 + MJPEG/静态服务 @ 7861)

浏览器打开 http://localhost:7860,**硬刷新**(Ctrl+Shift+R)拿最新前端资源。

**局域网访问**:UI 监听 0.0.0.0,同一 WiFi 下其他设备用
`http://<本机局域网IP>:7860` 打开即可(启动横幅会打印该链接)。
⚠️ 同网络的任何人都能控制机器人,请只在可信 WiFi 使用。

启动参数:

| 参数 | 作用 |
|---|---|
| `--ui` | Web UI(默认,可省略) |
| `--no-media` | daemon 不带媒体(排障用,无视频流) |
| `--daemon-only` | 只起 daemon |
| `--preload-datasets` | 预下载 HF 情感数据集(play_emotion 首次调用时用,普通对话不需要) |

### 真机模式

1. Reachy Mini 通电(有线版 USB 接 PC;无线版接好 RPi 并联网)
2. 顶栏「⚡ 连接真机」(有线)或下拉选「🌐 真机+仿真(无线)」
3. 连接成功后:真机麦克风/扬声器用于语音对话;「机器人视角」显示真机摄像头画面;手部跟随可用

> 提示:有线模式建议先把真机 USB 插好再点连接;后插也有热插拔自愈(3s 节流重探测)。

### macOS

⚠️ `reachy_mini` SDK 主要为 Linux 设计:macOS 可装 Python 依赖、跑单测,但 `reachy-mini-daemon --sim`(Mujoco + GStreamer)跑不了。要跑仿真请用 Linux 机器 / Docker / VM。

```bash
brew install cairo pkg-config
conda env create -f environment.yml
conda activate reachy
pip install -e ".[dev]"
pytest tests/smoke_test.py -v   # 部分用例依赖 GStreamer,可能跳过/失败
```

### Windows

⚠️ 预留适配(脚本模板:`scripts/install_deps.ps1`、`scripts/start.ps1`),尚未完整验证。思路:Visual Studio Build Tools + GStreamer + conda。欢迎 PR。

### 卸载

```bash
conda deactivate
conda env remove -n reachy
rm -rf ~/.reachymini        # API Key 等用户配置
pip uninstall reachymini_conversation
```

---

## English

### System requirements

| Item | Minimum | Recommended |
|---|---|---|
| OS | Ubuntu 22.04 / Debian 11 | Ubuntu 24.04 |
| Python | 3.11 | 3.12 (pinned in environment.yml) |
| conda | Miniconda or Anaconda | Miniconda |
| Disk | 5 GB | 10 GB |
| RAM | 4 GB | 8 GB |
| GPU | not required | not required (CPU-only) |
| Hardware | none (pure sim works) | Reachy Mini (USB wired / wireless RPi) |

### Linux (Ubuntu / Debian)

```bash
git clone https://github.com/TheMoonAstronaut/Reachy_Mini_conversation.git
cd Reachy_Mini_conversation

sudo ./scripts/install_deps.sh          # system libs (cairo/gstreamer/ffmpeg)

conda env create -f environment.yml     # if needed, install Miniconda first
conda activate reachy
pip install -e ".[dev]"

# write API keys to ~/.reachymini/env.json (see README.md / CONFIG.md)

# (optional) hand-follower model (~8 MB)
mkdir -p ~/.cache/reachymini
curl -L -o ~/.cache/reachymini/hand_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task

reachy-mini-conversation --help         # verify CLI
pytest tests/smoke_test.py -v           # verify install

./scripts/start.sh --ui                 # launch; open http://localhost:7860
```

No torch / no local ASR — speech recognition uses the cloud Doubao streaming API.

### macOS / Windows

The `reachy_mini` SDK is Linux-first. macOS can install Python deps and run unit tests, but `reachy-mini-daemon --sim` (Mujoco + GStreamer) requires Linux. Windows adaptation is scaffolded (`scripts/install_deps.ps1`, `scripts/start.ps1`) but not fully verified — PRs welcome.

### Uninstall

```bash
conda deactivate
conda env remove -n reachy
rm -rf ~/.reachymini
pip uninstall reachymini_conversation
```

---

## Next steps

- 配置:[`CONFIG.md`](CONFIG.md)
- 架构:[`ARCHITECTURE.md`](ARCHITECTURE.md)
- 故障排查:[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)
