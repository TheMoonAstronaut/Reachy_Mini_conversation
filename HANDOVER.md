# HANDOVER — 新对话上下文交接

> **目的**:让新对话的 AI 助手在 5 分钟内理解项目全貌,避免重复提问。
> **新对话第一步**:AI 助手必须读完 `agents.local.md`、`plan.md`、`P0_TASKS.md` 三份文档。
> **AGENTS.md 指引**:根据 `reachy_mini/AGENTS.md`,AI 助手开工前**必须**先查 `agents.local.md`。

---

## TL;DR(一句话)

把 `/home/seeed/Reachy_Mini_conversation`(豆包 CLI 对话)升级为 localhost Web 端,带 Mujoco 仿真 + 真机镜像 + 本地 FunASR ASR + 声源定位 + 手部跟随。**已锁定 16 项决策**,**已写完 8 阶段规划**,**P0 任务清单已就绪待执行**。

---

## 1. 项目基础信息

| 项 | 值 |
|---|---|
| 项目路径 | `/home/seeed/Reachy_Mini_conversation` |
| conda 环境 | `reachy` (Python 3.12) |
| `reachy_mini` 版本 | 1.10.0(全功能) |
| 用户硬件 | Linux + NVIDIA RTX 5070 8GB(本期仅 CPU) |
| 目标交付 | 开源到 GitHub,conda 一键配置,Windows 预留 |
| 当前阶段 | **P0 未开始**(3 个规划文档已就绪) |

---

## 2. 三个核心文档(必读)

```
/home/seeed/Reachy_Mini_conversation/
├── agents.local.md    ← 用户环境 + 16 决策锁定 + 硬约束(最先读)
├── plan.md            ← 8 阶段完整规划 + 架构图 + 关键代码示意
└── P0_TASKS.md        ← P0 详细任务清单(下一步要执行的)
```

**阅读顺序**:`agents.local.md` → `plan.md` → `P0_TASKS.md`

---

## 3. 16 项决策锁定(冻结,变更要走流程)

| # | 决策 | 选择 |
|---|---|---|
| 1 | Web 框架 | A. Gradio |
| 2/10 | Mujoco 仿真视图(主) | A. GStreamer UDP:5005 → MJPEG 推真实视频流 |
| 3 | 运行模式 | C. 默认 sim,可切真机 |
| 4 | 声源定位 UX | A. 后台读 DoA,VAD 触发驱动头部 |
| 5 | 手部跟随 UX | B. LLM 工具启停,默认关 |
| 6 | 对话 backend | FunASR SenseVoiceSmall ASR + 豆包 LLM + Edge TTS |
| 7 | 重构现有代码 | A. P0 全清去史山 |
| 8 | Hugging Face Spaces | A. 留 static/ 接口,本次不发布 |
| 9 | 真机↔Sim 镜像 | A. MirrorOrchestrator 双实例 |
| 11 | 真机摄像头 | A. MJPEG FastAPI `/camera_feed` |
| 12 | sim 模式副区域 | C. 占位图 |
| 13 | 本地中文 ASR | A. FunASR SenseVoiceSmall(~230 MB) |
| 14 | ASR 硬件后端 | A. CPU only |
| 15 | VAD 策略 | A. 删 main.py 简单 VAD,用 SenseVoice 自带 |
| 16 | play_emotion 工具 | D. 首次从 HF 下载,之后本地缓存 |

详细变更流程见 `agents.local.md §8`。

---

## 4. 8 阶段规划(plan.md §3)

| 阶段 | 目标 | 预计 |
|---|---|---|
| **P0** | 基础设施 + 去史山 + 开源仓库结构 | 1 天 |
| P1 | App 基类 + Web 框架(Gradio Blocks) | 0.5 天 |
| P2 | 模式切换 + Mujoco 视频流 + 真机↔sim 镜像 | 1 天 |
| P3 | 对话面板 + API Key 配置 | 0.5 天 |
| P4 | 语音管线接入 Web(FunASR + Brain + TTS) | 1.5 天 |
| P5 | 声源定位(AudioDoA) | 0.5 天 |
| P6 | 手部跟随(MediaPipe Hands + look_at_image) | 1 天 |
| P7 | LLM 工具完整化(真 function calling + play_emotion) | 1 天 |
| P8 | 收尾 + Windows 适配 + GitHub Actions CI | 0.5 天 |
| **合计** | | **~7.5 天** |

**当前**:P0 待执行,任务清单已就绪。

---

## 5. P0 要做的事(P0_TASKS.md 完整版)

**核心**:
1. 仓库结构改造(LICENSE、.gitignore、.env.example、environment.yml、scripts/)
2. 修复 `pyproject.toml` 补依赖 + 注册 CLI
3. 修复 `config.py` 改为读 `~/.reachymini/env.json`
4. **删重复实现**:`audio_animation/` 整目录 + `dance_emotion_moves.py` + 简化 `actions/move_queue.py` + 精简 `tools/`
5. 装新依赖:`funasr mediapipe av python-dotenv uvicorn`
6. 重写 README(中英双语)
7. 写 `docs/`(INSTALL/CONFIG/ARCHITECTURE/TROUBLESHOOTING)
8. 冒烟测试 `tests/smoke_test.py`
9. 兼容性验证(`python main.py` 仍能跑)

**关键验收**:`pytest tests/smoke_test.py` 全绿 + `python main.py` 不报错 + `pip install -e .` 一次成功 + 代码量减少 30%+

---

## 6. 关键约束(不可违反,agents.local.md §3)

1. **conda `reachy` 环境**,不动系统 Python
2. **运行时零 HF 联网**(决策 16D 例外:emotions 首次下载)
3. **Windows 适配预留**(目录结构、配置模板、文档)
4. **不开源到 HF Space**
5. **不引入 plan.md 未列出的新依赖**
6. **pyproject.toml 依赖必须完整**
7. **API Key 不入 git**(用 `~/.reachymini/env.json`)

---

## 7. 网络出口白名单(agents.local.md §4)

只有这 5 个端点允许远程调用:

| 端点 | 用途 | 时机 |
|---|---|---|
| `ark.cn-beijing.volces.com/api/v3/chat/completions` | 豆包 LLM | 每次对话 |
| `openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream` | 豆包 ASR | 每次对话(注:P4 后改本地) |
| `speech.platform.bing.com`(edge-tts) | Edge TTS | 每次 TTS |
| `modelscope.cn`(FunASR) | SenseVoiceSmall | **仅首次** |
| `huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library` | emotions dataset | **仅首次**(决策 16D) |

**白名单之外的一律禁止**,代码审查重点查 requests/httpx/websockets/urllib/subprocess 的远程目标。

---

## 8. 现有项目状态(代码现状)

### 当前文件结构
```
reachymini_conversation/
├── main.py                 # CLI 入口(将被 --ui 取代,P0 保留兼容)
├── config.py               # 配置(硬编码,P0 改为读 env.json)
├── brain.py                # 豆包 LLM(伪 function calling,P7 改造)
├── asr.py                  # 豆包 ASR WebSocket(P4 替换为 FunASR)
├── tts.py                  # Edge TTS(P0 保留)
├── audio.py                # 音频输入/输出
├── robot.py                # ReachyMini 单例
├── gradio_personality.py   # Gradio 雏形(P0 重构)
├── dance_emotion_moves.py  # Move 包装(P0 删除)
├── actions/
│   ├── move_queue.py       # MovementManager 自己实现(P0 大幅简化)
│   └── poses.py
├── audio_animation/        # head_wobbler + speech_tapper(P0 整目录删除)
├── tools/
│   ├── core_tools.py       # Tool ABC(P0 保留)
│   ├── dance.py
│   ├── move_head.py
│   ├── stop_dance.py
│   ├── idle_do_nothing.py
│   └── tool_constants.py
└── profiles/default/       # 人格配置
    ├── instructions.txt
    ├── tools.txt
    └── voice.txt
```

### 已知代码问题(P0 要修)
- README `cd reachymini_conversation` → `cd Reachy_Mini_conversation`(路径错)
- README 漏 `provider` 键(配置文档和实际不一致)
- pyproject.toml 漏 `edge-tts soundfile scipy numpy` 等 4 个包
- `MovementManager` 是 SDK 已有的功能重复实现
- `audio_animation/` 是 SDK `enable_wobbling()` 的重复造轮

---

## 9. 调研关键发现(背景知识)

| 发现 | 来源 |
|---|---|
| SDK 原生 `AudioDoA.get_DoA()` 声源定位 | `reachy_mini/media/audio_doa.py` |
| SDK 原生 `look_at_image(u, v)` 手部跟随 | `ReachyMini` 方法 |
| SDK 原生 `enable_wobbling()` 音频反应式摆动 | `media_manager.py` |
| SDK 原生 `ReachyMini(use_sim=True)` Mujoco 仿真 | `__init__` 参数 |
| daemon `--sim` 通过 UDP:5005 推 Mujoco 视频流 | `media_server.py` |
| SDK 1.10.0 装的是 GStreamer 后端 | 已装,无需额外配 |
| conda `reachy` 没装 `torch` `mediapipe` | P0.5 装 |
| 用户已有 `onnxruntime` 1.27.0 CPU 版 | P0 沿用,不装 GPU 版 |
| Hugging Face cache 已 30GB(其他项目),与本项目隔离 | 安全 |

### 官方参考(已调研)
- **`reachy_mini_conversation_app`**(GitHub 305★):Python + Gradio + 工具系统,本项目架构模板
- **`hand_tracker_v2`**(HF Space):手部跟随参考(MediaPipe Hands + look_at_image)
- **`reachy_mini/apps/`**:SDK 自带应用脚手架(`ReachyMiniApp` 基类)
- **`reachy_mini/AGENTS.md`**:SDK 官方 AI 助手开发指引

---

## 10. 下一步行动

**给新对话 AI 助手的指令**:

1. **先读 3 个核心文档**(`agents.local.md` → `plan.md` → `P0_TASKS.md`)
2. **跟用户确认 P0 是否开始执行**(默认假设:开始)
3. **按 P0_TASKS.md 顺序执行**,每完成一组任务贴进度
4. **关键节点前先 ping 用户**:
   - 删文件前
   - 改 `pyproject.toml` 前
   - 装新依赖前
   - 改 `config.py` 前
5. **完成后贴 P0 Done 总结**(改了哪些文件、删了多少行、新增了什么)

---

## 11. 协作约定

- **PR 标注阶段号**:如 `[P0] chore: remove duplicated MovementManager`
- **commit message**:`type(scope): summary` 格式,中文 OK
- **大改前先对齐 plan.md**
- **保守优先于激进**:有疑问就问,不假设

---

## 12. 一键恢复脚本(新对话开场用)

新对话开场,把下面这段发给 AI 助手即可:

```
我要继续推进 /home/seeed/Reachy_Mini_conversation 项目。
请按以下顺序阅读 3 个核心文档,然后给我下一步行动建议:

1. /home/seeed/Reachy_Mini_conversation/agents.local.md  (用户环境 + 16 决策锁定)
2. /home/seeed/Reachy_Mini_conversation/plan.md          (8 阶段规划)
3. /home/seeed/Reachy_Mini_conversation/P0_TASKS.md      (P0 详细任务清单)

阅读完请告诉我:
- 你理解到的项目目标和当前阶段
- P0 要做的核心事情有哪些
- 第一个要执行的具体任务是什么

记住:不要直接动代码,先跟我对齐。
```

---

## 13. 文件清单(交接时检查)

```bash
ls -la /home/seeed/Reachy_Mini_conversation/*.md
# 应输出:
# agents.local.md
# HANDOVER.md  (本文件)
# plan.md
# P0_TASKS.md
# README.md    (现有的,待 P0 重写)
```
