# INSTALL.md — 安装指南 / Installation Guide

> 适用版本:v1.0.0(P0 阶段)
> 决策记录:见 [`agents.local.md`](../agents.local.md)
> 计划更新:见 [`plan.md`](../plan.md)

---

## 中文

### 系统要求

| 项 | 最低 | 推荐 |
|---|---|---|
| OS | Ubuntu 22.04 / Debian 11 | Ubuntu 24.04 |
| Python | 3.11 | 3.12 |
| conda | Miniconda 或 Anaconda | Miniconda |
| 磁盘 | 5 GB | 10 GB(funasr 卸后实际 ~1 GB) |
| 内存 | 4 GB | 8 GB |
| GPU | 不需要 | 不需要(本期全部 CPU) |

### Linux(Ubuntu / Debian)

#### 步骤 1:克隆仓库

```bash
git clone https://github.com/<your-org>/Reachy_Mini_conversation.git
cd Reachy_Mini_conversation
```

> ⚠️ **目录名不要改**:很多脚本和文档默认仓库根叫 `Reachy_Mini_conversation`。

#### 步骤 2:装系统依赖

```bash
sudo ./scripts/install_deps.sh
```

这一步会用 apt 装:
- `cairo` / `gobject-introspection` / `pkg-config` — PyGObject 编译需要
- `gstreamer1.0` + 插件 — Mujoco 视频流 + 音频
- `portaudio` / `alsa` — 音频录制

如果脚本跑完没报 `gst-inspect-1.0`,说明 GStreamer 没装好,排查见 [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)。

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

这一步会装:
- `reachy_mini >= 1.10.0` — SDK
- `reachy_mini_dances_library` — 舞蹈库
- `numpy >= 2.2.5` / `scipy` / `soundfile` — 数值 / 音频
- `gradio` / `fastapi` / `uvicorn` — Web 框架
- `edge-tts` — Edge TTS
- `httpx` / `websockets` — 网络
- `mediapipe >= 1.0.0` — 手部跟随(P6 用)
- `av >= 10.0` — 视频 / 音频解码
- `python-dotenv` — 配置
- `[dev]` extras:`pytest` / `black` / `ruff` / `mypy`

**注意**:
- numpy >= 2.2.5 是 `reachy_mini 1.10.0` 的硬要求(决策 13/14 变更后,conda reachy 环境确保满足)
- 不装 torch(决策 14 + 无线版 RPi 兼容)
- 不装 FunASR / sherpa-onnx(决策 13:用云端豆包 ASR)

#### 步骤 5:验证安装

```bash
# CLI 注册成功
reachy-mini-conversation --help

# smoke test
pytest tests/smoke_test.py -v
```

如果看到 `18 passed`,P0 安装完成。

### macOS

> ⚠️ macOS 上 `reachy_mini` SDK 不直接支持(SDK 主要为 Linux 设计)。如需在 macOS 上开发/调试 UI,只能跑纯 Python 部分。

```bash
# 假设你用 Homebrew + Miniconda

# 1. 系统依赖(cairo / pkg-config 用 brew 装)
brew install cairo pkg-config gtk+3 libpng jpeg

# 2. conda + pip
conda env create -f environment.yml
conda activate reachy
pip install -e ".[dev]"

# 3. smoke test(部分测试可能跳过 macOS-only 行为)
pytest tests/smoke_test.py -v
```

`reachy-mini-daemon --sim` 在 macOS 上**不能跑**,因为 Mujoco 视频流和 GStreamer 后端有 Linux 依赖。如需在 macOS 上跑仿真,需要 Linux 环境(Docker / VM / 远程机器)。

### Windows

> ⚠️ **TODO(P8)**:Windows 适配预留,本 P0 阶段不强制完成。

预计实现思路(参考 Linux):

1. 装 [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/) (含 C++ 编译器)
2. 用 [Chocolatey](https://chocolatey.org/) 装系统依赖:
   ```powershell
   choco install -y gstreamer gtk-runtime python
   ```
3. 设置环境变量 `GST_PLUGIN_PATH` 指向 GStreamer plugins 目录
4. 用 `environment.yml` 建 conda 环境(conda-forge 在 Windows 上也支持)
5. `pip install -e ".[dev]"`

实际适配等用户在 Windows 上二次开发时再完成。

### Docker(未来)

> TODO(P8):提供 Dockerfile,一行 `docker run` 启动整个环境。

当前阶段(仅 Linux native)暂不实现。

### 卸载

```bash
# 退出环境
conda deactivate
conda env remove -n reachy

# 删用户配置(API Key)
rm -rf ~/.reachymini

# 卸包
pip uninstall reachymini_conversation
```

---

## English

### System requirements

| Item | Minimum | Recommended |
|---|---|---|
| OS | Ubuntu 22.04 / Debian 11 | Ubuntu 24.04 |
| Python | 3.11 | 3.12 |
| conda | Miniconda or Anaconda | Miniconda |
| Disk | 5 GB | 10 GB(~1 GB after dropping FunASR) |
| RAM | 4 GB | 8 GB |
| GPU | not required | not required (CPU-only) |

### Linux (Ubuntu / Debian)

```bash
git clone https://github.com/<your-org>/Reachy_Mini_conversation.git
cd Reachy_Mini_conversation

sudo ./scripts/install_deps.sh

# conda env (if you don't have conda, install Miniconda first)
conda env create -f environment.yml
conda activate reachy
pip install -e ".[dev]"

# verify
reachy-mini-conversation --help
pytest tests/smoke_test.py -v
```

### macOS

`reachy_mini` SDK is Linux-focused; macOS can run the Python parts but **not** `reachy-mini-daemon --sim` (Mujoco + GStreamer are Linux-only).

```bash
brew install cairo pkg-config gtk+3 libpng jpeg
conda env create -f environment.yml
conda activate reachy
pip install -e ".[dev]"
```

### Windows

> ⚠️ **TODO(P8)**: planned but not implemented yet. Will need Visual Studio Build Tools + Chocolatey + GStreamer.

### Uninstall

```bash
conda deactivate
conda env remove -n reachy
rm -rf ~/.reachymini
pip uninstall reachymini_conversation
```

---

## Next steps

- 配置 API Key: [`CONFIG.md`](CONFIG.md)
- 架构理解: [`ARCHITECTURE.md`](ARCHITECTURE.md)
- 故障排查: [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)