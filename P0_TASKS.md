# P0 — 基础设施与去史山(详细任务清单)

> **目标**:仓库结构开源化 + 依赖完整化 + 删重复实现 + 装新依赖 + 文档友好
> **预计工作量**:1 天(单人,4 小时专注)
> **前置**:无
> **产出**:仓库结构清晰、依赖完整、`python main.py` 仍能跑(兼容性)
> **依赖文档**:`agents.local.md`(决策锁定)、`plan.md`(全规划)

---

## 任务清单

### P0.1 仓库结构搭建

- [ ] **创建目录**:`docs/` `scripts/` `tests/` `reachymini_conversation/utils/` `reachymini_conversation/asr/` `reachymini_conversation/tts/` `reachymini_conversation/brain/`
- [ ] **写 `LICENSE`**:Apache 2.0(参考 SDK 的 LICENSE)
- [ ] **写 `.gitignore`**:
  ```
  __pycache__/
  *.pyc
  *.egg-info/
  .env
  .reachymini/
  *.local.json
  .cache/
  temp_*.wav
  output_*.wav
  .vscode/
  .idea/
  ```
- [ ] **写 `.env.example`**(参考 plan.md §4.5)
- [ ] **写 `environment.yml`**(plan.md §4.5 完整列出)
- [ ] **写 `requirements.txt`**(纯 pip,作为 environment.yml 备用)
- [ ] **写 `scripts/install_deps.sh`**(Linux apt install 一键)
- [ ] **写 `scripts/install_deps.ps1`**(Windows 预留,占位 + TODO 注释)
- [ ] **写 `scripts/start.sh`**(Linux:启动 daemon + app)
- [ ] **写 `scripts/start.ps1`**(Windows 预留)

### P0.2 修复 `pyproject.toml`

- [ ] **补依赖**(关键!现有漏包):
  ```toml
  dependencies = [
      "reachy_mini>=1.10.0",
      "reachy_mini_dances_library",
      "websockets",
      "httpx",
      "gradio>=4.0",
      "fastapi",
      "uvicorn",
      # 新增(决策 6/13 变更后,无 funasr)
      "edge-tts",
      "soundfile",
      "scipy",
      "numpy>=2.2.5",
      "mediapipe",
      "av>=10.0",
      "python-dotenv",
  ]
  ```
- [ ] **注册 CLI 入口**(`[project.scripts]`):
  ```toml
  [project.scripts]
  reachy-mini-conversation = "reachymini_conversation.app:main"
  ```
- [ ] **包发现**:`include` 改为 `["reachymini_conversation"]` 而不是分散的 `actions` `audio_animation` `tools`(这些要重组成子包)

### P0.3 修复 `config.py`

- [ ] **补 `provider` 键**(README 文档和代码不一致):
  ```python
  BRAIN_CONFIG = {
      "provider": "doubao",  # 已有
      "doubao": {...},
  }
  ```
- [ ] **改为读 `~/.reachymini/env.json`**:用 `python-dotenv` 或自写 `env_loader`
- [ ] **删 `ASR_CONFIG` 硬编码 Key**:运行时从 env.json 读
- [ ] **删 `TTS_CONFIG` 硬编码**:运行时从 env.json 读
- [ ] **加 `RUN_MODE` 配置**(默认 `pure_sim`)
- [ ] ~~**加 `FUNASR_CONFIG`**~~ (2025-09-09 移除:不再使用本地 ASR,豆包 ASR 用现有 `ASR_CONFIG["api_key"]`)

### P0.4 删重复实现

- [ ] **删除 `audio_animation/` 整个目录**:
  - SDK `enable_wobbling()` 已覆盖(head_wobbler.py 200+ 行重复)
  - 删除前确认 SDK API 行为一致
- [ ] **删除 `dance_emotion_moves.py`**:
  - SDK `DanceMove` 已覆盖
  - 我们自己包装的 `DanceQueueMove` `GotoQueueMove` 也不需要
- [ ] **简化 `actions/move_queue.py` → `actions/movement.py`**:
  - 保留 `MovementManager` 的接口,**内部全部用 SDK `play_move` / `goto_target` / `set_target`**
  - 删除 `BreathingMove` / `DanceQueueMove` / `EmotionQueueMove` / `GotoQueueMove` 全部自实现
  - 保留 `combine_full_body_pose` 等纯数学工具函数
- [ ] **精简 `tools/`**:
  - 保留 `dance.py` `move_head.py` `stop_dance.py` `idle_do_nothing.py`
  - 新增 `look_at_sound.py`(占位,P5 实现)
  - 删除 `tool_constants.py` 的冗余枚举
  - 保留 `Tool` ABC + `dispatch_tool_call`

### P0.5 安装新依赖

```bash
conda activate reachy
pip install "reachy-mini>=1.10.0" "reachy-mini-dances-library"
pip install -e .

# 新增(决策 6/13 变更后,无 funasr)
pip install mediapipe        # ~12MB pip wheel, 模型内置
pip install av               # ~5MB
pip install python-dotenv    # ~50KB
pip install uvicorn          # ~30MB
```

> **变更 2025-09-09**:FunASR 相关依赖移除(决策 6/13),不需要装 `funasr` / `funasr-onnx`,也不用装 torch。MediaPipe / av / python-dotenv / uvicorn 保留(供 P6 手部跟随 + P2 MJPEG 视频流用)。

- [ ] **验证豆包 ASR WebSocket SDK 可用**(决策 6 沿用现有 `asr.py`):
  ```python
  # 现有 asr.py 的 DoubaoASR,只需要 api_key + url 即可连接
  # 验证模块能 import 即可,真实连接需要 ~wss://~ 网络
  from reachymini_conversation.asr import doubao_asr  # P0.4 后路径,P0 阶段验证根目录 asr.py
  import asr
  print(asr.DoubaoASR)  # 类存在即可
  ```
- [ ] **验证 MediaPipe Hands 可用**:
  ```python
  import mediapipe as mp
  hands = mp.solutions.hands.Hands()
  print("OK")
  ```
- [ ] **冒烟测试**:确认所有包都能 import

### P0.6 修复 README

- [ ] **删除 front matter**(Wiki 导出格式,GitHub 不需要)
- [ ] **修复 `cd reachymini_conversation` → `cd Reachy_Mini_conversation`**(仓库名不一致)
- [ ] **重写为中英双语**:
  - 顶部中英标题
  - 一键安装命令(突出)
  - 截图/GIF 占位
- [ ] **章节重组**:
  - 简介
  - 效果展示(占位)
  - 系统架构图(简版)
  - 安装(Linux 详细 + Windows 预留)
  - 配置(API Key / 模型 / 模式)
  - 启动
  - 进阶(本地 ASR、声源、手部跟随)
  - 故障排查(链接 docs/)
  - 许可证 / 贡献

### P0.7 写 `docs/`

- [ ] **`docs/INSTALL.md`**:
  - Linux 详细步骤(Ubuntu/Debian)
  - macOS 提示
  - Windows 占位章节(标 TODO)
- [ ] **`docs/CONFIG.md`**:
  - `~/.reachymini/env.json` 字段说明
  - 各模型参数(豆包 LLM / 豆包 ASR / Edge TTS)
  - 模式切换说明
- [ ] **`docs/ARCHITECTURE.md`**:
  - 简化版 plan.md §2
  - 模块图
  - 数据流
- [ ] **`docs/TROUBLESHOOTING.md`**:
  - conda 装不上
  - ReSpeaker 固件升级
  - GStreamer 缺失
  - Mujoco 视频流端口被占
  - 豆包 ASR 连接失败 / API Key 错误

### P0.8 冒烟测试

- [ ] **写 `tests/smoke_test.py`**:
  ```python
  def test_import():
      import reachy_mini
      from reachy_mini import ReachyMini
      from reachy_mini.media.audio_doa import AudioDoA
      assert reachy_mini.__version__ >= "1.10.0"

  def test_deps():
      # 决策 6/13 变更(2025-09-09):移除 funasr,豆包 ASR 走 WebSocket
      import mediapipe, edge_tts, gradio, fastapi
      import av, dotenv, uvicorn, websockets, httpx
      assert all([mediapipe, edge_tts, gradio, fastapi,
                  av, dotenv, uvicorn, websockets, httpx])

  def test_config_loads():
      from reachymini_conversation.config import load_config
      cfg = load_config()  # 读 ~/.reachymini/env.json,不存在返回默认
      assert "doubao_llm" in cfg

  def test_main_help():
      import subprocess
      result = subprocess.run(
          ["python", "-m", "reachymini_conversation", "--help"],
          capture_output=True
      )
      assert result.returncode == 0
  ```
- [ ] **跑测试**:`pytest tests/` 全绿

### P0.9 GitHub Actions(可选,P0 不强制)

- [ ] **写 `.github/workflows/ci.yml`**(lint + smoke test):
  ```yaml
  name: CI
  on: [push, pull_request]
  jobs:
    test:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: conda-incubator/setup-miniconda@v3
          with:
            environment-file: environment.yml
            activate-environment: reachy
        - shell: bash -el {0}
          run: |
            conda activate reachy
            pip install -e .
            pytest tests/
  ```

### P0.10 兼容性验证

- [ ] **跑 `python main.py`**(原 CLI 入口)确认仍能起:
  - 会显示 deprecation warning 提示用 `--ui`
  - 不报错
- [ ] **跑 `python -m reachymini_conversation --ui`**:
  - 打开浏览器 `localhost:7860`
  - 看到状态徽章 + Gradio 界面(可能是空对话面板)
- [ ] **跑 `python tests/smoke_test.py`**:
  - 全绿

---

## 验收标准(P0 Done)

### 仓库结构

- [ ] 目录布局符合 `plan.md §2.2`
- [ ] `LICENSE` 是 Apache 2.0
- [ ] `.gitignore` 完整
- [ ] `.env.example` 模板可用

### 依赖

- [ ] `environment.yml` 一键创建 conda 环境成功
- [ ] `pip install -e .` 一次成功
- [ ] `pyproject.toml` 依赖完整(不漏 edge-tts / mediapipe / av 等)
- [ ] `reachy-mini-conversation` CLI 命令注册成功

### 代码精简

- [ ] `audio_animation/` 已删除
- [ ] `dance_emotion_moves.py` 已删除
- [ ] `actions/move_queue.py` 简化(行数减少 50%+)
- [ ] `tools/` 精简到 5 个

### 新功能可用

- [ ] ~~FunASR SenseVoiceSmall 模型下载到 `~/.cache/modelscope/`~~ (2025-09-09 移除)
- [ ] MediaPipe Hands 能 import
- [ ] AV (PyAV) 能 import

### 测试

- [ ] `pytest tests/smoke_test.py` 全绿
- [ ] `python main.py` 仍能起(deprecation warning 可接受)
- [ ] `python -m reachymini_conversation --ui` 能起

### 文档

- [ ] README 中英双语
- [ ] 4 个 docs/*.md 写完(INSTALL/CONFIG/ARCHITECTURE/TROUBLESHOOTING)
- [ ] 一键安装命令清晰

### 决策一致性

- [ ] `agents.local.md` 16 个决策与 plan.md 一致
- [ ] `config.py` 符合 plan.md §4.5 模板

---

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| FunASR 首次下载失败 | ~~已不适用~~(2025-09-09 决策 6/13 变更) |
| MediaPipe 在某 Linux 发行版装不上 | 加 fallback 注释,提示用 conda-forge |
| 删除 `audio_animation/` 后现有 main.py 跑不通 | **P0.4 前先备份**;若炸,先保留作为可选依赖 |
| `pyproject.toml` 包发现改了之后装不上 | 先 dry-run `pip install -e . --dry-run` |

---

## 完成 P0 后的下一步

P0 完成后,我会:
1. 在 chat 里贴 P0 完成总结(改了哪些文件、删了多少行、新增了什么)
2. 等用户确认 P0 Done 后,**进入 P1**(App 基类 + Web 框架)
3. 每个阶段开始前先列出该阶段任务清单,等用户审阅
