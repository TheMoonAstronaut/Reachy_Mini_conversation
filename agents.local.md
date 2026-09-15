# agents.local.md — 用户环境与项目决策

> 这是 AI 助手的会话级上下文,记录用户环境、项目约束、所有锁定的决策。
> 任何代码改动前必须先读本文件。每次 PR 必须在描述里引用对应的阶段号(P0–P9)。

---

## 1. 用户环境

| 项 | 值 |
|---|---|
| OS | Linux (Ubuntu/Debian),后续会适配 Windows |
| Shell 工作目录 | `/home/seeed/Reachy_Mini_conversation` |
| conda 环境 | `reachy` (Python 3.12) |
| `reachy_mini` 版本 | 1.10.0 |
| `reachy_mini_dances_library` | 已装 |
| GPU | NVIDIA RTX 5070 8GB(本期**仅用 CPU**,不装 onnxruntime-gpu) |
| `torch` | 未装(FunASR 会顺带装) |
| `onnxruntime` | 1.27.0 (CPU 版) |
| HF cache | `~/.cache/huggingface/`(30GB,均为其他项目数据,与本项目隔离) |
| ModelScope cache | 不存在,FunASR 首次会下载 SenseVoiceSmall(~230MB)到 `~/.cache/modelscope/` |
| git remote | 待定(准备开源到 GitHub) |

### 系统依赖(Linux)

```
libcairo2-dev libgirepository1.0-dev pkg-config python3-dev
libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev
gstreamer1.0-plugins-good gstreamer1.0-plugins-bad
```

### Windows 适配预留(待用户在 Windows 上二次适配)

- 见 `scripts/install_deps.ps1`(占位)
- 见 `docs/INSTALL.md` Windows 章节(占位)
- 不在 P0 强制要求,只留结构

---

## 2. 16 项决策锁定(冻结,变更见 §9 变更日志)

| # | 决策项 | 选择 |
|---|---|---|
| 1 | Web 框架 | **A. Gradio** |
| 2 | Mujoco 仿真视图(主) | **A. GStreamer UDP:5005 → MJPEG 推真实视频流** |
| 3 | 运行模式 | **C. 默认 sim,可切真机** |
| 4 | 声源定位 UX | **A. 后台持续读 DoA,VAD 触发时驱动头部转向** |
| 5 | 手部跟随 UX | **B. LLM 工具启停,默认关** |
| 6 | 对话 backend | **豆包 ASR(WebSocket 流式) + 豆包 LLM + Edge TTS** `[变更:2025-09-09,见 §9]` |
| 7 | 重构现有代码 | **A. P0 全清去史山** |
| 8 | Hugging Face Spaces | **A. 留 static/ 接口,本次不发布** |
| 9 | 真机↔Sim 镜像机制 | **A. MirrorOrchestrator(sim + real 双实例)** |
| 10 | sim 视图传输 | = 决策 2(我表里重复列了,以 2 为准) |
| 11 | 真机摄像头画面 | **A. MJPEG FastAPI `/camera_feed`** |
| 12 | sim 模式副区域显示 | **C. 占位图(""显示"")** |
| 13 | 本地中文 ASR | **B. 不引入本地 ASR,沿用云端豆包 ASR 流式 API** `[变更:2025-09-09,见 §9]` |
| 14 | ASR 硬件后端 | **B. 不适用(无本地 ASR 推理)** `[变更:2025-09-09,见 §9]` |
| 15 | VAD 策略 | **B. 保留 main.py 简单能量 VAD(简化+可后续替换)** `[变更:2025-09-09,见 §9]` |
| 16 | play_emotion 工具 | **D. 首次从 HF 下载 emotions dataset,之后本地缓存** |

---

## 3. 硬约束(不可违反)

1. **conda `reachy` 环境**:所有操作在此环境内,**不动系统 Python**
2. **运行时零 HF 联网**:`reachy-mini-daemon` 启动**不**加 `--preload-datasets` 也能跑核心功能;**只有决策 16D(play_emotion)**允许首次 HF 下载,之后本地缓存
3. **Windows 适配预留**:目录结构、配置模板、文档必须考虑 Windows 路径(用 `pathlib.Path` 不用 `os.path`),但不强制 P0 完整适配
4. **不开源到 HF Space**:只预留 `static/` 接口供以后打包
5. **不引入 plan.md 未列出的新依赖**
6. **pyproject.toml 依赖必须完整**:不能漏 `edge-tts soundfile scipy numpy funasr mediapipe av python-dotenv`
7. **API Key 不入 git**:用 `~/.reachymini/env.json`(用户级配置),仓库只放 `.env.example`

---

## 4. 网络出口白名单(项目允许的远程端点)

| 端点 | 用途 | 触发时机 |
|---|---|---|
| `ark.cn-beijing.volces.com/api/v3/chat/completions` | 豆包 LLM | 每次对话 |
| `openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream` | 豆包 ASR(WebSocket 流式) | 每次对话 |
| `speech.platform.bing.com`(edge-tts) | Edge TTS | 每次 TTS |
| `huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library` | emotions dataset | **仅首次**(决策 16D) |

**白名单之外的远程调用一律禁止**,代码审查时重点查 `requests`、`httpx`、`websockets`、`urllib`、`subprocess` 的远程目标。

---

## 5. 协作约定

- **每次 PR 标注阶段号**:如 `[P0] chore: remove duplicated MovementManager`
- **不引入未在 plan.md 里规划的新依赖**:如确需新增,在 PR 描述里说理由
- **测试**:核心模块必须有 smoke test(import + dry-run)
- **commit message**:`type(scope): summary` 格式,中文 OK
- **大改前先对齐 plan.md**

---

## 6. 用户偏好

- 默认推荐组合优先("全默认"组合作为兜底)
- 中文文档优先(英文镜像)
- 代码注释中文为主,关键 API 英文
- **保守优先于激进**:有疑问就问,不假设

---

## 7. 项目背景

- **基础项目**:`/home/seeed/Reachy_Mini_conversation`(豆包 LLM + 豆包 ASR 流式 WebSocket + Edge TTS,Gradio 雏形)
- **目标**:升级为 localhost Web 端,左侧 Mujoco 仿真 + 真机↔sim 镜像,右侧对话 + 配置
- **新增能力**:声源定位(SDK `AudioDoA`)、手部跟随(MediaPipe Hands + SDK `look_at_image`)
- **开源目标**:GitHub 公开仓库,conda 一键安装,Windows 预留适配

---

## 8. 决策变更流程

任何决策要变更时:
1. 在 `agents.local.md` 里改决策项 + 标注"变更日期 + 原因"
2. 在 `plan.md` 对应阶段章节里标注"已变更"
3. 在 PR 描述里说清楚影响范围
4. **绝不静默修改**:变更要可追溯

---

## 9. 决策变更日志

按 §8 流程记录所有决策变更。最新变更在最上方。

### 2025-09-09 批次变更(无线版兼容 + 回到豆包 ASR)

| 决策 | 变更前 | 变更后 | 原因 |
|---|---|---|---|
| **6** 对话 backend | FunASR ASR + 豆包 LLM + Edge TTS | 豆包 ASR(WebSocket 流式)+ 豆包 LLM + Edge TTS | (1) 调研官方 `reachy_mini_conversation_app`:完全不用本地 ASR,走 HF Realtime API 或本地 `speech-to-speech` 后端(后者需 torch,无法跑 RPi);(2) 调研 `funasr` 1.4.12 实际依赖:虽 metadata 不列 torch,但 `from funasr import AutoModel` 硬要 torch,装上就 ~750MB+;(3) `funasr-onnx` 0.4.2 pin numpy<=1.26.4 与 `reachy_mini` 要 numpy>=2.x 互斥;(4) 用户已有 onnxruntime 1.27.0 CPU,但 sherpa-onnx 仍是 ~14MB 的 C++ 运行时,集成复杂度高;(5) 现有 `asr.py` 已经实现 `DoubaoASR` WebSocket 流式,功能验证过。**决定回到最初的豆包 ASR 方案**,既避免 FunASR 的 torch 包袱,又保留 P0 全清去史山(代码已经实现) |
| **13** 本地中文 ASR | A. FunASR SenseVoiceSmall(~230 MB) | **B. 不引入本地 ASR,沿用云端豆包 ASR 流式 API** | 同上(决策 6)。原决策基于"本地化 + 离线"假设,但实际场景是 PC 上跑应用,RPi 只跑 daemon(无线版架构),所以"本地 ASR"在 RPi 上跑没意义;在 PC 上跑又何必绕一圈 FunASR |
| **14** ASR 硬件后端 | A. CPU only(不装 onnxruntime-gpu) | **B. 不适用(无本地 ASR 推理)** | 决策 13 变更后无本地 ASR 推理,此项失去意义,标记为不适用。豆包 ASR 是云端 API,无硬件后端选择 |
| **15** VAD 策略 | A. 删 main.py 简单能量 VAD,用 SenseVoice 自带 VAD | **B. 保留 main.py 简单能量 VAD(P0 简化清理,P4 阶段视情况替换)** | 决策 13 变更后没有 SenseVoice 自带 VAD 可用。能量 VAD 是最简实现且 P0 兼容(决策 7 优先)。后续可升级到 WebRTC VAD / silero-vad,但 P0 不强制 |

**关联变更**(非决策表项,但同步调整):
- `pyproject.toml` / `environment.yml` / `requirements.txt`:**移除** `funasr` / `funasr-onnx` 两个包
- `.env.example` / `env_loader.py` DEFAULT_ENV:**移除** `funasr` 块
- `config.py`:**移除** `FUNASR_CONFIG`(API Key 仍走 `ASR_CONFIG["api_key"]` = env.json 的 `doubao_asr.api_key`)
- 网络出口白名单 §4:**移除** `modelscope.cn`(决策 16D 例外不变)
- 影响 P0 验收 §6:"FunASR 模型下载"那一条删除
- 影响 P0_TASKS.md P0.5:"验证 FunASR 首次下载"那一条删除

**后续 PR 描述要写明**:本批次变更撤回了"本地化 ASR"的规划,改回项目最初的"豆包 ASR WebSocket 流式"方案,目的是避免 torch 依赖、保持无线版 RPi 兼容、降低 P0 集成复杂度。
