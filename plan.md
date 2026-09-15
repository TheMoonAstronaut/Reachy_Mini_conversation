# Reachy Mini 对话 Web 应用 — 完整规划(plan.md)
>
> **项目代号**:`reachy-mini-conversation`(沿用)
> **文档状态**:v1.0(2025-09-09 全部 8 阶段完成)
> **决策锁定**:见 [`agents.local.md`](agents.local.md) + §9 变更日志
> **当前阶段**:✅ P0-P7 完成(P8 收尾中)

---

## 1. 项目目标

把现有 CLI 对话应用 `/home/seeed/Reachy_Mini_conversation` 升级为**完整 localhost Web 端**,新增 4 类能力:

1. **Web 端 + Mujoco 仿真 + 真机↔sim 实时镜像**
2. **真实语音对话反馈**(豆包 LLM + **豆包 ASR WebSocket 流式** + Edge TTS)
3. **respeaker 声源定位**(头部转向声源)
4. **眼睛摄像头手部跟随**(MediaPipe Hands + SDK `look_at_image`)

**附加约束**:
- 完全跳过 HF dataset 运行时调用(决策 16D 例外)
- 开源到 GitHub,conda 一键配置
- Windows 系统预留适配位置
- **无线版兼容**(决策 6/13 变更 2025-09-09:不引入 torch / FunASR / 本地 ASR,详见 `agents.local.md §9`)

---

## 2. 架构总览

### 2.1 分层

```
┌──────────────────────────────────────────────────────────────┐
│  Web 层 (Gradio @ localhost:7860)                            │
│  ┌──────────────────────────┐  ┌─────────────────────────┐  │
│  │ Mujoco 视频流(主)        │  │ 模式切换 / 人格 / 设置   │  │
│  │ (GStreamer→MJPEG)        │  │                         │  │
│  ├──────────────────────────┤  │ 对话面板                │  │
│  │ 摄像头画面(副)           │  │ • Chatbot              │  │
│  │ pure_sim: 占位           │  │ • 状态徽章              │  │
│  │ real+sim: 真机 MJPEG     │  │ • 输入框                │  │
│  └──────────────────────────┘  └─────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
              ↕ HTTP / WebSocket
┌──────────────────────────────────────────────────────────────┐
│  App 进程 (reachy_mini.apps.ReachyMiniApp 子类)             │
│                                                              │
│   ┌─────────────────┐    ┌──────────────────────────┐        │
│   │ MirrorOrchestr. │    │ VoicePipeline            │        │
│   │ ┌─────┐ ┌─────┐ │    │ FunASR → Brain → TTS    │        │
│   │ │ sim │ │real │ │    └──────────────────────────┘        │
│   │ │mini │ │mini │ │                                        │
│   │ └──┬──┘ └──┬──┘ │    ┌──────────────────────────┐        │
│   │    └────┬────┘    │    │ ToolRegistry            │        │
│   │   镜像调用        │    │ dance/move_head/        │        │
│   └─────────────────┘    │ look_at_sound/play_em...│        │
│                          └──────────────────────────┘        │
│   ┌─────────────────┐    ┌──────────────────────────┐        │
│   │ SoundLocalizer  │    │ HandFollower(opt-in)     │        │
│   │ (AudioDoA 后台) │    │ (MediaPipe 后台)         │        │
│   └─────────────────┘    └──────────────────────────┘        │
│   ┌──────────────────────────────────────────────────┐       │
│   │ StateBus:asyncio.Queue + WebSocket 广播           │       │
│   │ (head_rpy / doa_angle / hand_xy / mode / ...)     │       │
│   └──────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────┘
              ↕ SDK 高阶 API
┌──────────────────────────────────────────────────────────────┐
│  reachy_mini 1.10.0                                          │
│  • ReachyMini(use_sim=True/False)                            │
│  • MediaManager(get_frame / get_frame_jpeg / get_audio)      │
│  • AudioDoA(get_DoA → angle_rad + speech)                   │
│  • look_at_image / look_at_world / play_move / wobbling     │
└──────────────────────────────────────────────────────────────┘
              ↕
┌──────────────────────────────────────────────────────────────┐
│  Hardware / Sim                                              │
│  • reachy-mini-daemon --sim(UDP:5005 视频流,可选)             │
│  • 真机 Reachy(USB)                                          │
│  • ReSpeaker XVF3800(声源定位)                               │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 仓库结构(开源版)

```
Reachy_Mini_conversation/                   ← GitHub 公开仓库
├── environment.yml                        ← conda 环境定义(主)
├── requirements.txt                       ← 纯 pip 备用
├── pyproject.toml                         ← 包定义 + CLI 入口
├── README.md                              ← 中英双语,一键安装/启动
├── LICENSE                                ← Apache 2.0(SDK 一致)
├── agents.local.md                        ← 用户决策 + 环境(本仓库包含示例)
├── plan.md                                ← 本文件
├── P0_TASKS.md                            ← P0 详细任务清单
├── .env.example                           ← API Key 模板
├── .gitignore                             ← 排除敏感+临时
├── reachymini_conversation/                ← 代码包
│   ├── __init__.py
│   ├── app.py                             ← ReachyMiniApp 子类
│   ├── web_ui.py                          ← Gradio Blocks
│   ├── state_bus.py                       ← asyncio.Queue + 广播
│   ├── mirror_orchestrator.py             ← 双实例镜像
│   ├── sound_localizer.py                 ← AudioDoA 包装
│   ├── hand_follower.py                   ← MediaPipe 包装
│   ├── voice_pipeline.py                  ← FunASR + Brain + TTS
│   ├── config.py                          ← 读 ~/.reachymini/env.json
│   ├── brain/                             ← 豆包 LLM + 工具系统
│   │   ├── __init__.py
│   │   ├── doubao_brain.py
│   │   └── tools/                         ← Tool ABC + 5 个工具
│   ├── asr/                               ← FunASR 包装
│   │   ├── __init__.py
│   │   └── funasr_asr.py
│   ├── tts/                               ← Edge TTS 包装
│   │   ├── __init__.py
│   │   └── edge_tts.py
│   ├── profiles/                          ← 默认人格
│   │   └── default/
│   │       ├── profile.md                 ← TOML frontmatter + Markdown
│   │       ├── tools.txt
│   │       └── voice.txt
│   └── utils/
│       ├── env_loader.py                  ← 读 ~/.reachymini/env.json
│       └── camera_stream.py               ← MJPEG FastAPI 推流
├── scripts/
│   ├── install_deps.sh                    ← Linux 系统依赖
│   ├── install_deps.ps1                   ← Windows 系统依赖(预留)
│   ├── start.sh                           ← Linux 一键启动
│   └── start.ps1                          ← Windows 一键启动(预留)
├── docs/
│   ├── INSTALL.md                         ← 详细安装(Linux + Windows)
│   ├── CONFIG.md                          ← API Key / 模型 / 模式
│   ├── ARCHITECTURE.md                    ← 架构详解
│   └── TROUBLESHOOTING.md                 ← 常见问题
└── tests/
    ├── smoke_test.py                      ← 冒烟测试
    └── test_config.py                     ← 配置加载测试
```

### 2.3 数据流(一次语音轮次)

```
麦克风(ReachyMini.media.get_audio_sample,16kHz mono)
   │
   ▼  客户端能量 VAD(P0:沿用 main.py 简单阈值;后续可升级 WebRTC VAD)
   │
   │  检测到语音 → 流式发往豆包 ASR(WebSocket)
   ▼  豆包 ASR 流式(`openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream`)
   │  服务器侧 VAD + 标点 + ITN
文本(中文)
   │
   ▼  DoubaoBrain(豆包方舟 chat/completions,真 function calling)
   │
   ├─→ 文本回复 → Edge TTS → 扬声器
   │
   └─→ ToolCalls:
       ├─ dance / move_head / stop_dance(已有,精简)
       ├─ play_emotion(决策 16D,首次 HF 下载)
       ├─ look_at_sound(新增,转向声源)
       └─ start_hand_follow / stop_hand_follow(新增,启停手部跟随)
              │
              ▼
       MirrorOrchestrator.set_target / play_move
              │
              ├─→ sim_mini.set_target(...)  ─→ Mujoco 仿真
              └─→ real_mini.set_target(...) ─→ 真机(若 RUN_MODE=real_plus_sim)
                            ↑
              ┌─────────────┴──────────────┐
              │                            │
       SoundLocalizer           HandFollower(LLM 启用后)
       AudioDoA 后台线程         MediaPipe Hands 后台线程
       speech 触发时直接          look_at_image(u,v)
       set_target 头部转向
```

> **注**:决策 6/13 变更后(2025-09-09),**没有本地 ASR**。所有 ASR 走豆包云端 WebSocket 流式 API。客户端能量 VAD 仅用于"开始/停止录音"触发,语义识别由云端处理。

### 2.4 进程模型

- **主进程**:FastAPI(Gradio 嵌入)+ asyncio 主循环
- **后台工作线程**(每个一个 `threading.Thread`,通过 `StateBus` 桥到 asyncio):
  - 麦克风采集 + FunASR 识别(`asyncio.run_coroutine_threadsafe`)
  - TTS 播放
  - `SoundLocalizer`(读 DoA)
  - `HandFollower`(读摄像头帧 + MediaPipe)
  - 真机摄像头 MJPEG 推流
  - Mujoco 视频流 GStreamer→MJPEG 推流
- **状态广播**:`StateBus` → WebSocket `/ws` → 前端订阅
- **SDK `MovementManager` 自己实现 / `head_wobbler.py` 全部删除**,用 SDK 高阶 API

---

## 3. 8 阶段开发规划

> 每阶段必须**可独立运行 + 验证**。避免一次写完才测。
> **强约束**:阶段内所有 PR 标注 `[P<n>]`。

| 阶段 | 目标 | 关键产出 | 验收命令 | 状态 |
|---|---|---|---|---|
| **P0** | 基础设施 + 去史山 + 开源仓库结构 | 删 `audio_animation/`、精简 `actions/`、补 `pyproject.toml`、写 `environment.yml`、装 `funasr mediapipe av`、写 README/docs/.gitignore/LICENSE、冒烟测试 | `pip install -e .` + `python tests/smoke_test.py` 全绿 | ✅ |
| **P1** | App 基类 + Web 框架 | `app.py` 继承 `ReachyMiniApp`、`web_ui.py` Gradio Blocks、显示头部 RPY 数值、状态徽章 | `python -m reachymini_conversation --ui` 打开 7860,数字滚动 | ✅ |
| **P2** | 模式切换 + Mujoco 视频流 + 真机↔sim 镜像 | `mirror_orchestrator.py`、`config.py` 加 `RUN_MODE`、GStreamer pipeline 收 UDP:5005 → MJPEG `/sim_feed` | `--sim` 起 sim,Web 看到真实 Mujoco 视频流;加 `--real` 切换真机+sim | ✅ |
| **P3** | 对话面板 + API Key 配置 | Gradio Chatbot、`config.py` 读 `~/.reachymini/env.json`、UI 设置面板写 env.json | UI 改 API Key → 重启后 LLM 调用走新 Key | ✅ |
| **P4** | 语音管线接入 Web | `voice_pipeline.py`(豆包 ASR WebSocket + Brain + Edge TTS)、`StateBus` 广播"听/想/说/动"事件 | UI 看到完整轮次:输入→识别→LLM→TTS→头部动 | ✅ |
| **P5** | 声源定位 | `sound_localizer.py`(包装 `AudioDoA`)、后台线程、VAD 触发、`look_at_sound` 工具 | 真机模式说"嗨",机器人转头;UI 显当前角度 | ✅ |
| **P6** | 手部跟随 | `hand_follower.py`(MediaPipe 1.0+ tasks API)、`start_hand_follow` / `stop_hand_follow` 工具、UI 启停按钮 | UI 启动"跟随手",真机模式下挥手,机器人头部跟随 | ✅ |
| **P7** | LLM 工具完整化 | `brain/` 改造传 `tools=` 真 function calling、8 个工具完整、新增 `play_emotion`(决策 16D,首次下载 emotions) | UI 显示 LLM 主动调用工具的轨迹;`--preload-datasets` 首次拉 emotions | ✅ |
| **P8** | 收尾/Windows 适配/发布 | `scripts/install_deps.ps1`(占位)、`docs/INSTALL.md` Windows 章节(占位)、GitHub Actions CI 严格 lint、README 双语、ruff+black | `pip install -e .` 在 Windows PowerShell(如有)能跑;CI 绿 | 🟡 |

### 3.1 阶段详细说明

#### P0 — 基础设施与去史山(预计 1 天)

详见 `P0_TASKS.md`。核心:
- 仓库结构改造
- pyproject.toml 补齐依赖
- 删 `audio_animation/`(SDK `enable_wobbling` 已覆盖)
- 简化 `actions/move_queue.py`(去掉自己实现,只用 SDK `play_move`/`goto_target`)
- 精简 `tools/` 到 5 个工具
- 装 funasr / mediapipe / av / python-dotenv
- 写开源友好的 README + LICENSE + .gitignore + .env.example
- 冒烟测试

#### P1 — App 基类 + Web 框架(预计 0.5 天)

- 继承 `ReachyMiniApp`
- Gradio Blocks:`gr.Markdown`(状态徽章) + `gr.JSON`(RPY 数值,1Hz 刷新)
- `StateBus` 雏形(只发 head_rpy)
- `--ui` 启动

#### P2 — 模式切换 + Mujoco 视频流 + 镜像(预计 1 天)

- `MirrorOrchestrator` 双实例
- `RUN_MODE` 配置(pure_sim / real_plus_sim)
- GStreamer pipeline:`udpsrc port=5005 ! rtpvrawdepay ! videoconvert ! videorate ! jpegenc ! appsink`
- FastAPI `/sim_feed` MJPEG 推流
- 前端 `<img src="/sim_feed">` 嵌入 Gradio

#### P3 — 对话面板 + API Key 配置(预计 0.5 天)

- Gradio Chatbot(只读,不接 ASR)
- 设置面板:豆包 API Key / Model ID / TTS voice
- `~/.reachymini/env.json` 读写工具
- `config.py` 改为读 env.json(不硬编码)

#### P4 — 语音管线接入 Web(预计 1.5 天)

- `doubao_asr.py`:包装豆包 ASR WebSocket 流式 API,沿用现有 `asr.py` 的 `DoubaoASR` 协议,3 个方法:connect / send_audio / close
- `voice_pipeline.py`:协调 ASR → Brain → TTS
- 客户端 VAD:沿用现有 `main.py` 能量阈值(P4 不强制替换);后续可升级 WebRTC VAD / silero-vad
- `StateBus` 广播:"listening" / "thinking" / "speaking" / "moving"
- Web 端:状态徽章根据事件变色

> **变更 2025-09-09**:原计划用 FunASR SenseVoiceSmall(本地),现改为豆包 ASR 云端 WebSocket 流式。原因见 `agents.local.md §9`。变更影响:
> - ✅ 节省 ~750MB torch 依赖 + ~230MB FunASR 模型
> - ✅ P0 不再需要装 funasr / funasr-onnx
> - ✅ 现有 `asr.py` 的 `DoubaoASR` 实现复用,P4 主要工作是把它包成 `doubao_asr.py` 模块 + 接 StateBus
> - ⚠️ VAD 不再由 SenseVoice 自带,客户端 VAD 需要保留或升级(决策 15)

#### P5 — 声源定位(预计 0.5 天)

- `sound_localizer.py`:包装 `AudioDoA`,10 Hz 读
- speech detected → `set_target_head_pose`(平滑,避开 goto_target 队列)
- 静音 3 秒 → 回中位
- `look_at_sound` 工具(LLM 可主动调)
- UI 显示当前角度

#### P6 — 手部跟随(预计 1 天)

- `hand_follower.py`:MediaPipe Hands,30 Hz 处理
- 检测到手掌中心 → `look_at_image(u, v, duration=0.3)`
- 两个工具:`start_hand_follow` / `stop_hand_follow`
- UI 启停按钮 + 状态徽章
- 默认关,显式开启

#### P7 — LLM 工具完整化(预计 1 天)

- `brain/doubao_brain.py` 改造:`payload['tools'] = ALL_TOOL_SPECS`
- 5 个工具完整(去伪 function calling)
- `play_emotion` 工具(决策 16D):首次启动 `daemon --preload-datasets`
- UI 显示工具调用轨迹(透明化)
- 替换"中文括号正则"为真 function calling

#### P8 — 收尾 + Windows 适配 + 发布(预计 0.5 天)

- `scripts/install_deps.ps1`(占位,标 TODO)
- `docs/INSTALL.md` Windows 章节(占位)
- GitHub Actions `.github/workflows/ci.yml`(lint + smoke)
- README 清理
- 准备开源说明

### 3.2 总时间估算

| 阶段 | 工作量 |
|---|---|
| P0 | 1 天 |
| P1 | 0.5 天 |
| P2 | 1 天 |
| P3 | 0.5 天 |
| P4 | 1.5 天 |
| P5 | 0.5 天 |
| P6 | 1 天 |
| P7 | 1 天 |
| P8 | 0.5 天 |
| **合计** | **~7.5 天**(单人,每天 4 小时专注) |

---

## 4. 关键技术决策细节

### 4.1 MirrorOrchestrator(决策 9)

```python
class MirrorOrchestrator:
    """双 ReachyMini 实例镜像,根据 RUN_MODE 决定是否调用 real。"""

    def __init__(self, run_mode: Literal["pure_sim", "real_plus_sim"]):
        self.sim_mini = ReachyMini(use_sim=True, media_backend="default")
        self.real_mini: ReachyMini | None = None
        if run_mode == "real_plus_sim":
            self.real_mini = ReachyMini(use_sim=False, media_backend="default")

    async def goto_target(self, head=None, antennas=None,
                           duration=0.5, body_yaw=0.0):
        await asyncio.gather(
            self.sim_mini.goto_target(head, antennas, duration, body_yaw),
            self.real_mini.goto_target(head, antennas, duration, body_yaw)
            if self.real_mini else asyncio.sleep(0),
        )

    async def play_move(self, move):
        await asyncio.gather(
            self.sim_mini.play_move(move),
            self.real_mini.play_move(move)
            if self.real_mini else asyncio.sleep(0),
        )

    def set_target(self, **kwargs):
        self.sim_mini.set_target(**kwargs)
        if self.real_mini:
            self.real_mini.set_target(**kwargs)
```

### 4.2 SoundLocalizer(决策 4)

```python
class SoundLocalizer:
    """基于 SDK AudioDoA 的后台声源定位。"""

    def __init__(self, orch: MirrorOrchestrator, hz: int = 10):
        from reachy_mini.media.audio_doa import AudioDoA
        self.doa = AudioDoA()
        self.orch = orch
        self._stop = threading.Event()
        self._last_speech_time = 0.0

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.is_set():
            r = self.doa.get_DoA()
            if r and r[1]:  # speech detected
                angle_rad, _ = r
                target_yaw_deg = math.degrees(angle_rad) - 90
                self.orch.set_target(body_yaw=target_yaw_deg)
                self._last_speech_time = time.time()
            elif time.time() - self._last_speech_time > 3.0:
                self.orch.set_target(body_yaw=0.0)  # 回中
            time.sleep(0.1)
```

### 4.3 HandFollower(决策 5)

```python
class HandFollower:
    """基于 MediaPipe Hands 的手部跟随,默认关。"""

    def __init__(self, orch: MirrorOrchestrator):
        import mediapipe as mp
        self.hands = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            min_detection_confidence=0.5,
        )
        self.orch = orch
        self._stop = threading.Event()
        self._enabled = False
        self._lock = threading.Lock()

    def enable(self):
        with self._lock:
            self._enabled = True

    def disable(self):
        with self._lock:
            self._enabled = False

    def _loop(self):
        while not self._stop.is_set():
            with self._lock:
                if not self._enabled:
                    time.sleep(0.1)
                    continue
            frame = self.orch.sim_mini.media.get_frame()
            if frame is None:
                continue
            rgb = frame[:, :, ::-1]
            res = self.hands.process(rgb)
            if res.multi_hand_landmarks:
                palm = res.multi_hand_landmarks[0].landmark[9]
                h, w = frame.shape[:2]
                u, v = int(palm.x * w), int(palm.y * h)
                self.orch.sim_mini.look_at_image(u, v, duration=0.3)
                if self.orch.real_mini:
                    self.orch.real_mini.look_at_image(u, v, duration=0.3)
            time.sleep(0.033)  # 30 Hz
```

### 4.4 VoicePipeline(决策 6)

```python
class VoicePipeline:
    """豆包 ASR(WebSocket 流式)→ Brain → TTS 的协调器。"""

    def __init__(self, asr: DoubaoASRClient, brain: DoubaoBrain, tts: EdgeTTS,
                 audio_in: ReachyAudioInput, orch: MirrorOrchestrator,
                 state_bus: StateBus):
        self.asr = asr
        self.brain = brain
        self.tts = tts
        self.audio = audio_in
        self.orch = orch
        self.bus = state_bus

    async def run_once(self):
        # 1. 客户端 VAD 检测到语音 → 流式发往豆包 ASR
        await self.bus.emit("listening")
        await self.asr.connect()
        async for partial in self.asr.stream_recognize(self.audio.stream()):
            if partial.is_final:
                text = partial.text
                break
        await self.asr.close()

        # 2. Brain
        await self.bus.emit("thinking")
        result = await self.brain.query(text)
        await self.bus.emit("speaking", text=result.reply)

        # 3. TTS + 工具执行
        if result.reply:
            wav = self.tts.synthesize(result.reply)
            await self.bus.emit("playing_audio")
            self.orch.sim_mini.media.push_audio_sample(wav)
            for tool_call in result.tool_calls:
                await self.orch.dispatch_tool(tool_call)
```

### 4.5 env.json 模板

```json
{
  "doubao_llm": {
    "api_key": "your-ark-api-key",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "model": "doubao-seed-character-251128"
  },
  "doubao_asr": {
    "api_key": "your-asr-api-key",
    "resource_id": "volc.seedasr.sauc.duration"
  },
  "edge_tts": {
    "voice": "zh-CN-XiaoxiaoNeural"
  },
  "run_mode": "pure_sim",
  "hf_preload_datasets": false
}
```

> **变更 2025-09-09**:`funasr` 块已移除(决策 13)。豆包 ASR 配置保留 `api_key` + `resource_id`。`hf_preload_datasets` 默认 `false`(决策 16D)。

---

## 5. 风险与边界

| 风险 | 等级 | 缓解 |
|---|---|---|
| 用户环境没有真机 ReSpeaker XVF3800 | 高 | P5 仿真模式可降级(没有 DoA 数据,UI 显 N/A),不阻塞 P6/P7 |
| MediaPipe Hands 在低配 CPU 上卡 | 中 | P6 默认关,启动后显式开关;30 Hz 帧率可降 |
| Mujoco 视频流在 daemon `--sim` 下的 GStreamer 绑定 | 中 | P2 先用 SDK 状态轮询验证可达,再上 GStreamer |
| 豆包 ASR WebSocket 协议细节 | 低 | 现有 `asr.py` 已跑通,只需替换实现 |
| 豆包 ASR 云端延迟 / 断网 | 中 | 客户端加重试 + UI 显示状态;P5+ 可加本地命令词兜底 |
| Windows GStreamer 配置复杂 | 中 | P8 只预留占位,实际 Windows 适配用户自己做 |
| GitHub Actions 没有 GStreamer/Libcairo | 中 | CI 只跑 smoke test(import),不跑实际机器人 |
| `requirements.txt` vs `pyproject.toml` 不一致 | 低 | P0 同步两份文件 |

> **变更 2025-09-09**:
> - ❌ 删除"FunASR 首次下载失败"行 — 不再使用 FunASR
> - ✅ 新增"豆包 ASR 云端延迟 / 断网"行 — 改成云端 ASR 后必须考虑降级

---

## 6. 验收检查表(P0 结束)

- [ ] `conda env create -f environment.yml && conda activate reachy` 一键创建
- [ ] `pip install -e .` 一次成功
- [ ] `python tests/smoke_test.py` 全绿
- [ ] `python main.py` 仍能跑(兼容性,会显示 deprecation warning)
- [ ] `python -m reachymini_conversation --help` 输出正常
- [ ] 现有功能(豆包 LLM + ASR + Edge TTS)仍可用
- [ ] `audio_animation/` 已删除,`actions/move_queue.py` 简化
- [ ] `pyproject.toml` 依赖完整
- [ ] README 中英双语,一键安装命令清晰
- [ ] `.gitignore` 排除敏感+临时
- [ ] `LICENSE` 是 Apache 2.0
- [ ] 代码量较 P0 前减少 30%+

---

## 7. 后续可能的扩展(不在本期)

- HF Space 打包发布(决策 8)
- 多个 Reachy 机器人同时连接(AGENTS.md 提到)
- 视频流 WebRTC 化(替代 MJPEG)
- GPU 加速 ASR(装 onnxruntime-gpu)
- 语音唤醒词检测("嗨,Reachy")
- 多人脸跟踪(SDK `start_head_tracking` 已有基础)
