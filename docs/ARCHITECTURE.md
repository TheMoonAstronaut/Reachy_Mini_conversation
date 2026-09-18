# ARCHITECTURE.md — 架构说明 / Architecture Reference

> 设计文档:[`plan.md`](../plan.md) / [`SPEC_V2.md`](../SPEC_V2.md)

---

## 中文

### 分层架构

```
┌──────────────────────────────────────────────────────────────────┐
│  Web 层 (Gradio @ localhost:7860)                                │
│  ┌──────────────────────────────┐  ┌─────────────────────────┐    │
│  │ Mujoco 视频流(主)            │  │ 模式切换 / 人格 / 设置   │    │
│  │ GStreamer UDP:5005 → MJPEG   │  │                         │    │
│  ├──────────────────────────────┤  │ 对话面板                │    │
│  │ 摄像头画面(副)               │  │ • Chatbot              │    │
│  │ pure_sim: 占位图             │  │ • 状态徽章              │    │
│  │ real+sim: 真机 MJPEG FastAPI │  │ • 输入框                │    │
│  └──────────────────────────────┘  └─────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
                ↕ HTTP / WebSocket
┌──────────────────────────────────────────────────────────────────┐
│  App 进程 (reachy_mini.apps.ReachyMiniApp 子类)                 │
│                                                                  │
│   ┌─────────────────────┐    ┌──────────────────────────┐        │
│   │ MirrorOrchestrator  │    │ VoicePipeline            │        │
│   │ ┌─────┐ ┌─────┐     │    │ 豆包 ASR → 豆包 LLM →    │        │
│   │ │ sim │ │real │     │    │ Edge TTS                  │        │
│   │ │mini │ │mini │     │    └──────────────────────────┘        │
│   │ └──┬──┘ └──┬──┘     │                                        │
│   │    └────┬────┘      │    ┌──────────────────────────┐        │
│   │   镜像调用         │    │ ToolRegistry             │        │
│   └─────────────────────┘    │ dance / move_head /      │        │
│                              │ look_at_sound /          │        │
│   ┌─────────────────────┐    │ play_emotion /           │        │
│   │ SoundLocalizer       │    │ start_hand_follow /      │        │
│   │ (AudioDoA 后台)      │    │ stop_hand_follow         │        │
│   └─────────────────────┘    └──────────────────────────┘        │
│   ┌─────────────────────┐    ┌──────────────────────────┐        │
│   │ HandFollower (P6)   │    │ StateBus                │        │
│   │ (mediapipe 后台)    │    │ asyncio.Queue + WebSocket│        │
│   └─────────────────────┘    └──────────────────────────┘        │
└──────────────────────────────────────────────────────────────────┘
                ↕ SDK 高阶 API
┌──────────────────────────────────────────────────────────────────┐
│  reachy_mini 1.10.0                                              │
│  • ReachyMini(use_sim=True/False)                                │
│  • MediaManager(get_frame / get_frame_jpeg / get_audio)          │
│  • AudioDoA(get_DoA → angle_rad + speech)                       │
│  • enable_wobbling / disable_wobbling                            │
│  • look_at_image / look_at_world / play_move / goto_target       │
└──────────────────────────────────────────────────────────────────┘
                ↕
┌──────────────────────────────────────────────────────────────────┐
│  硬件 / 仿真                                                     │
│  • reachy-mini-daemon --sim(UDP:5005 视频流,Mujoco)              │
│  • 真机 Reachy(USB / 无线 WiFi)                                  │
│  • ReSpeaker XVF3800(声源定位)                                   │
└──────────────────────────────────────────────────────────────────┘
```

### 模块职责

| 模块 | 文件 | 职责 | 阶段 |
|---|---|---|---|
| **App 基类** | `reachymini_conversation/app.py` | 继承 `ReachyMiniApp`,Gradio Blocks 入口 | P1 |
| **Web UI** | `reachymini_conversation/web_ui.py` | Gradio Blocks:Mujoco 视频 + Chatbot + 设置 | P1 |
| **State Bus** | `reachymini_conversation/state_bus.py` | asyncio.Queue + WebSocket 广播 | P1 |
| **MirrorOrchestrator** | `reachymini_conversation/mirror_orchestrator.py` | sim + real 双实例镜像 | P2 |
| **SoundLocalizer** | `reachymini_conversation/sound_localizer.py` | AudioDoA 后台线程 + VAD 触发 | P5 |
| **HandFollower** | `reachymini_conversation/hand_follower.py` | mediapipe 1.0+ tasks API + look_at_image | P6 |
| **VoicePipeline** | `reachymini_conversation/voice_pipeline.py` | 豆包 ASR → 豆包 LLM → Edge TTS | P4 |
| **doubao_asr** | `reachymini_conversation/asr/doubao_asr.py` | 现有 `asr.py` 的模块化迁移 | P4 |
| **doubao_brain** | `reachymini_conversation/brain/doubao_brain.py` | 现有 `brain.py` 模块化 + 真 function calling | P7 |
| **edge_tts** | `reachymini_conversation/tts/edge_tts.py` | 现有 `tts.py` 模块化 | P4 |
| **config** | `config.py` | 读 `~/.reachymini/env.json` | **P0 ✅** |
| **env_loader** | `reachymini_conversation/utils/env_loader.py` | env.json 加载 / 保存 / 校验 | **P0 ✅** |
| **camera_stream** | `reachymini_conversation/utils/camera_stream.py` | MJPEG FastAPI 推流 | P2 |

### 数据流(一次语音轮次)

```
麦克风(ReachyMini.media.get_audio_sample, 16kHz mono)
   │
   ▼  客户端能量 VAD(决策 15:沿用 main.py 简单阈值)
   │  检测到语音 → 流式发往豆包 ASR(WebSocket)
   ▼  豆包 ASR(决策 6:wss://openspeech.bytedance.com/...)
   │  服务端 VAD + 标点 + ITN
文本(中文)
   │
   ▼  DoubaoBrain(豆包方舟 chat/completions,真 function calling,P7)
   │
   ├─→ 文本回复 → Edge TTS → 扬声器
   │
   └─→ ToolCalls(P7 真 function calling):
       ├─ dance / move_head / stop_dance(已有,P0.4 精简)
       ├─ play_emotion(决策 16D,首次 HF 下载)
       ├─ look_at_sound(P5 新增)
       └─ start_hand_follow / stop_hand_follow(P6 新增)
              │
              ▼
       MirrorOrchestrator.set_target / play_move(P2)
              │
              ├─→ sim_mini.set_target(...)  ─→ Mujoco 仿真
              └─→ real_mini.set_target(...) ─→ 真机(若 RUN_MODE=real_plus_sim)
                            ↑
              ┌─────────────┴──────────────┐
              │                            │
       SoundLocalizer           HandFollower(P6)
       AudioDoA 后台线程         mediapipe 后台线程
       speech 触发时直接         look_at_image(u, v)
       set_target 头部转向
```

> **决策 6/13 变更(2025-09-09)**:无本地 ASR。所有 ASR 走豆包云端 WebSocket 流式 API。
> **P0 状态**:Web UI / MirrorOrchestrator / VoicePipeline 等模块文件尚未创建,
> P1+ 按 `plan.md §3` 阶段顺序实装。

### 进程模型

- **主进程**:FastAPI(Gradio 嵌入)+ asyncio 主循环
- **后台工作线程**(每个一个 `threading.Thread`,通过 `StateBus` 桥到 asyncio):
  - 麦克风采集 + 豆包 ASR 流式(`asyncio.run_coroutine_threadsafe`)
  - TTS 播放
  - `SoundLocalizer`(读 DoA)
  - `HandFollower`(读摄像头帧 + mediapipe)
  - 真机摄像头 MJPEG 推流
  - Mujoco 视频流 GStreamer→MJPEG 推流
- **状态广播**:`StateBus` → WebSocket `/ws` → 前端订阅

### 关键设计决策(从 plan.md §4 摘)

#### MirrorOrchestrator(决策 9)

```python
class MirrorOrchestrator:
    """双 ReachyMini 实例镜像,根据 RUN_MODE 决定是否调用 real。"""

    def __init__(self, run_mode: Literal["pure_sim", "real_plus_sim"]):
        self.sim_mini = ReachyMini(use_sim=True, media_backend="default")
        self.real_mini: ReachyMini | None = None
        if run_mode == "real_plus_sim":
            self.real_mini = ReachyMini(use_sim=False, media_backend="default")

    async def goto_target(self, head=None, ..., body_yaw=0.0):
        await asyncio.gather(
            self.sim_mini.goto_target(head, ..., body_yaw),
            self.real_mini.goto_target(head, ..., body_yaw)
            if self.real_mini else asyncio.sleep(0),
        )
```

#### SoundLocalizer(决策 4)

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

#### HandFollower(决策 5)

```python
class HandFollower:
    """基于 MediaPipe Hands 的手部跟随,默认关。"""

    def __init__(self, orch: MirrorOrchestrator):
        # P0.5 验证:mediapipe 1.0+ 用新 tasks API
        # (plan.md §4.3 用的是旧 solutions API,实装 P6 时会同步)
        from mediapipe.tasks.python import vision
        from mediapipe.tasks import python
        self.hand_landmarker = vision.HandLandmarker.create_from_options(...)
        self.orch = orch
        self._stop = threading.Event()
        self._enabled = False
        self._lock = threading.Lock()

    def enable(self): self._enabled = True
    def disable(self): self._enabled = False

    def _loop(self):
        while not self._stop.is_set():
            with self._lock:
                if not self._enabled:
                    time.sleep(0.1); continue
            frame = self.orch.sim_mini.media.get_frame()
            if frame is None: continue
            rgb = frame[:, :, ::-1]
            res = self.hand_landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            if res.hand_landmarks:
                palm = res.hand_landmarks[0][9]
                h, w = frame.shape[:2]
                u, v = int(palm.x * w), int(palm.y * h)
                self.orch.sim_mini.look_at_image(u, v, duration=0.3)
                if self.orch.real_mini:
                    self.orch.real_mini.look_at_image(u, v, duration=0.3)
            time.sleep(0.033)  # 30 Hz
```

#### VoicePipeline(决策 6)

```python
class VoicePipeline:
    """豆包 ASR(WebSocket 流式) → Brain → TTS 的协调器。"""

    def __init__(self, asr: DoubaoASRClient, brain: DoubaoBrain, tts: EdgeTTS,
                 audio_in: ReachyAudioInput, orch: MirrorOrchestrator,
                 state_bus: StateBus):
        self.asr = asr; self.brain = brain; self.tts = tts
        self.audio = audio_in; self.orch = orch; self.bus = state_bus

    async def run_once(self):
        # 1. 流式 ASR(客户端 VAD 触发 + 服务端 VAD 结束)
        await self.bus.emit("listening")
        text = await self.asr.stream_recognize(self.audio.stream())

        # 2. Brain
        await self.bus.emit("thinking")
        result = await self.brain.query(text)
        await self.bus.emit("speaking", text=result.reply)

        # 3. TTS + 工具执行
        if result.reply:
            wav = self.tts.synthesize(result.reply)
            self.orch.sim_mini.media.push_audio_sample(wav)
            for tool_call in result.tool_calls:
                await self.orch.dispatch_tool(tool_call)
```

### 仓库结构(P1+ 完整态)

```
Reachy_Mini_conversation/                   ← GitHub 公开仓库
├── environment.yml                        ← conda 环境定义(主)
├── requirements.txt                       ← 纯 pip 备用
├── pyproject.toml                         ← 包定义 + CLI 入口
├── README.md                              ← 中英双语 ✅
├── LICENSE                                ← Apache 2.0 ✅
├── plan.md                                ✅ 设计文档
├── SPEC_V2.md                             ✅ 设计文档
├── .env.example                           ✅
├── .gitignore                             ✅
├── reachymini_conversation/                ← 代码包(P0.1 建骨架)
│   ├── __init__.py
│   ├── app.py                             ← P0.2 shim ✅,P1 实装
│   ├── web_ui.py                          ← P1 实装
│   ├── state_bus.py                       ← P1
│   ├── mirror_orchestrator.py             ← P2
│   ├── sound_localizer.py                 ← P5
│   ├── hand_follower.py                   ← P6
│   ├── voice_pipeline.py                  ← P4
│   ├── config.py                          ✅(留在根)
│   ├── brain/                             ← P7
│   │   ├── __init__.py
│   │   └── doubao_brain.py
│   ├── asr/                               ← P4
│   │   ├── __init__.py
│   │   └── doubao_asr.py
│   ├── tts/                               ← P4
│   │   ├── __init__.py
│   │   └── edge_tts.py
│   └── utils/
│       ├── env_loader.py                  ✅
│       └── camera_stream.py               ← P2
├── tools/                                  ← P0.4 精简 ✅
│   ├── core_tools.py
│   ├── dance.py
│   ├── move_head.py
│   ├── stop_dance.py
│   ├── idle_do_nothing.py
│   ├── look_at_sound.py                   ← P5 占位 ✅
│   └── __init__.py
├── scripts/
│   ├── install_deps.sh                    ✅
│   ├── install_deps.ps1                   ✅(占位)
│   ├── start.sh                           ✅
│   └── start.ps1                          ✅(占位)
├── docs/                                   ← P0.7 ✅
│   ├── INSTALL.md
│   ├── CONFIG.md
│   ├── ARCHITECTURE.md                    ← 本文
│   └── TROUBLESHOOTING.md
└── tests/
    └── smoke_test.py                      ✅(冒烟)
```

### 架构约束(决策一致性)

- **网络白名单**:只允许 4 类远程端点 —— 豆包 LLM(`ark.cn-beijing.volces.com`)/ 豆包 ASR(`openspeech.bytedance.com`)/ Edge TTS(`speech.platform.bing.com`)/ HF emotions dataset(`huggingface.co`,仅首次)
- **零 torch**(无线版 RPi 兼容)
- **零本地 ASR**(决策 13 变更后)
- **API Key 不入 git**(决策 7 + 约束 7)

---

## English

### Layered architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Web layer (Gradio @ localhost:7860)                              │
│  └ Mujoco video (main) / Camera (sub) / Chat panel / Settings    │
└──────────────────────────────────────────────────────────────────┘
              ↕
┌──────────────────────────────────────────────────────────────────┐
│  App process                                                     │
│  MirrorOrchestrator │ VoicePipeline                              │
│  SoundLocalizer │ HandFollower │ ToolRegistry │ StateBus        │
└──────────────────────────────────────────────────────────────────┘
              ↕
┌──────────────────────────────────────────────────────────────────┐
│  reachy_mini 1.10.0 SDK                                          │
│  ReachyMini │ MediaManager │ AudioDoA │ look_at_image │ play_move│
└──────────────────────────────────────────────────────────────────┘
              ↕
┌──────────────────────────────────────────────────────────────────┐
│  Hardware / simulation                                           │
│  reachy-mini-daemon --sim (Mujoco + GStreamer UDP:5005)          │
│  ReSpeaker XVF3800 │ Reachy Mini hardware (USB or WiFi)          │
└──────────────────────────────────────────────────────────────────┘
```

### Module responsibilities

| Module | Stage | Note |
|---|---|---|
| App base / `app.py` | P0.2 + P1 | Inherits `ReachyMiniApp`, Gradio Blocks |
| Web UI / `web_ui.py` | P1 | Gradio Blocks: Mujoco + Chatbot + Settings |
| State Bus / `state_bus.py` | P1 | asyncio.Queue + WebSocket broadcast |
| MirrorOrchestrator | P2 | sim + real dual instance |
| SoundLocalizer | P5 | AudioDoA background + VAD-trigger |
| HandFollower | P6 | MediaPipe 1.0+ tasks API + look_at_image |
| VoicePipeline | P4 | Doubao ASR → Doubao LLM → Edge TTS |
| config / env_loader | **P0 ✅** | Reads `~/.reachymini/env.json` |
| movement shim | **P0 ✅** | MovementManager compat shim |

### Data flow (one voice turn)

See Chinese section above for the full diagram.

### Architecture invariants

- Network whitelist: only the remote endpoints declared in README(4 类,见中文版)
- Zero torch (RPi compatibility)
- Zero local ASR (decision 13)
- API keys never committed (constraint 7)

### 路线图 / Roadmap

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0–P2 | 基础设施 + App 骨架 + Web UI + 仿真镜像 | ✅ 完成 |
| P3–P4 | 对话面板 + 豆包 ASR 语音管线 | ✅ 完成 |
| P5–P7 | 声源定位 + 手部跟随 + LLM 工具调用 | ✅ 完成 |
| P8 | Windows 完整适配 + CI 强化 | 🟡 预留(脚本模板已就位) |

---

## Related

- Design docs: [`plan.md`](../plan.md) / [`SPEC_V2.md`](../SPEC_V2.md)
- Install: [`INSTALL.md`](INSTALL.md)
- Config: [`CONFIG.md`](CONFIG.md)
- Troubleshooting: [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)