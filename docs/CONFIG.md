# CONFIG.md — 配置说明 / Configuration Reference

> 决策记录:见 [`agents.local.md`](../agents.local.md) §2
> 配置文件:`~/.reachymini/env.json`(用户级,Windows 对应 `%USERPROFILE%\.reachymini\env.json`)

---

## 中文

### 配置文件位置

```
~/.reachymini/env.json    ← Linux
%USERPROFILE%\.reachymini\env.json   ← Windows
```

**不会入 git**(已在 `.gitignore` 排除)。

**首次使用前**必须创建并填 API Key,否则:
- 豆包 LLM:启动时 WARNING,但程序仍可启动(API 调用时会 401)
- 豆包 ASR:启动时 WARNING,但程序仍可启动(连接时会失败)
- 其他:有默认值,不报错

### 完整配置模板

```json
{
  "doubao_llm": {
    "api_key": "your-ark-api-key-here",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "model": "doubao-seed-character-251128"
  },
  "doubao_asr": {
    "api_key": "your-asr-api-key-here",
    "resource_id": "volc.seedasr.sauc.duration"
  },
  "edge_tts": {
    "voice": "zh-CN-XiaoxiaoNeural"
  },
  "run_mode": "pure_sim",
  "hf_preload_datasets": false
}
```

### 字段说明

#### `doubao_llm`(豆包大语言模型)

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `api_key` | str | `""` | 必填。火山引擎方舟控制台申请:https://www.volcengine.com/docs/6561/1354869 |
| `base_url` | str | `https://ark.cn-beijing.volces.com/api/v3` | API 端点(决策 6) |
| `model` | str | `doubao-seed-character-251128` | 模型 ID。常用选项:`doubao-seed-character-251128`(角色扮演推荐)/ `doubao-pro-32k` / `doubao-lite-32k` |

#### `doubao_asr`(豆包流式语音识别)

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `api_key` | str | `""` | 必填。火山引擎语音技术申请:https://www.volcengine.com/product/asr |
| `resource_id` | str | `volc.seedasr.sauc.duration` | 资源 ID,常用值固定 |

> **决策 6 + 13**:用豆包 WebSocket 流式 ASR(`openspeech.bytedance.com`),**不需要本地 ASR 模型**。
> 端点(白名单 §4):`wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream`

#### `edge_tts`(Edge 文字转语音)

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `voice` | str | `zh-CN-XiaoxiaoNeural` | 声音 ID。常用中文声音:|
| | | | • `zh-CN-XiaoxiaoNeural`(晓晓,温柔女声) |
| | | | • `zh-CN-YunxiNeural`(云希,男声) |
| | | | • `zh-CN-YunyangNeural`(云扬,男主播) |
| | | | • `zh-CN-XiaoyiNeural`(晓伊,女童) |
| | | | • `zh-CN-liaoning-XiaobeiNeural`(辽宁话) |
| | | | • `zh-CN-shaanxi-XiaoniNeural`(陕北话) |
| | | | 完整列表:https://speech.microsoft.com/portal/voicegallery |

> **注意**:edge-tts 是微软的免费 API,**需要网络访问 `speech.platform.bing.com`**(白名单 §4)。

#### `run_mode`(运行模式,决策 3)

| 值 | 含义 | 需要 |
|---|---|---|
| `pure_sim` | 仅仿真,默认 | Mujoco 仿真 daemon |
| `real_plus_sim` | 真机 + 仿真镜像 | USB 真机 + 真机 daemon |

切换模式:
```bash
# 临时
./scripts/start.sh --real

# 永久:写 env.json
```

#### `hf_preload_datasets`(决策 16D)

| 值 | 含义 |
|---|---|
| `false`(默认)| 跳过,`play_emotion` 工具不可用 |
| `true` | 首次启动从 `huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library` 下载 ~230 MB(白名单 §4)|

下载完成后会缓存到 `~/.cache/huggingface/`,**只有首次会联网**。

#### `provider`(LLM provider,决策 6)

| 值 | 含义 |
|---|---|
| `doubao`(默认)| 豆包 LLM |

> 当前只支持豆包。后续可能扩展(本地 Ollama / OpenAI 兼容 / 自托管)。

### 配置热加载

P3 之后,UI 设置面板可以在线修改 env.json。Python 端需要调 `config.reload_config()` 才会读到新值。当前 P0 阶段改完 env.json 需要重启进程。

### 配置文件权限

写入时 `env_loader.py` 强制 `chmod 0600`(仅当前用户可读写):
```bash
-rw------- 1 seeed seeed 312 Sep 9 10:38 /home/seeed/.reachymini/env.json
```

敏感 API Key 文件强烈建议保持 0600 权限。

### 不在 env.json 中的配置

| 项 | 配置方式 | 说明 |
|---|---|---|
| Python 包依赖 | `pyproject.toml` | 改后 `pip install -e .` |
| 系统原生依赖 | `scripts/install_deps.sh` | Linux only |
| conda 环境 | `environment.yml` | 改后 `conda env update -f environment.yml` |
| 行为开关(决策 1-16) | `agents.local.md` | 冻结,变更要走 §8 流程 |

---

## English

### Config file location

```
~/.reachymini/env.json         ← Linux
%USERPROFILE%\.reachymini\env.json   ← Windows
```

**Never committed** (excluded by `.gitignore`).

### Full template

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

### Field reference

| Key | Type | Default | Description |
|---|---|---|---|
| `doubao_llm.api_key` | str | `""` | **Required**. Volcano Engine Ark console |
| `doubao_llm.base_url` | str | `https://ark.cn-beijing.volces.com/api/v3` | API endpoint (decision 6) |
| `doubao_llm.model` | str | `doubao-seed-character-251128` | Model ID (role-play recommended) |
| `doubao_asr.api_key` | str | `""` | **Required**. Volcano Engine ASR console |
| `doubao_asr.resource_id` | str | `volc.seedasr.sauc.duration` | Resource ID, fixed for now |
| `edge_tts.voice` | str | `zh-CN-XiaoxiaoNeural` | Voice ID (see MS voice gallery) |
| `run_mode` | str | `pure_sim` | `pure_sim` \| `real_plus_sim` |
| `hf_preload_datasets` | bool | `false` | Pre-download HF emotions on first run |

> **Decision 6 + 13**: Doubao WebSocket streaming ASR (`openspeech.bytedance.com`) — **no local ASR model needed**.

### Hot reload

P3+ will add an in-UI settings panel that calls `config.reload_config()`. Until then, edit env.json and restart the process.

### File permissions

`env_loader.save_env()` enforces `chmod 0600` on the file. Keep it that way — API keys are sensitive.

---

## Related

- Network egress whitelist: [`agents.local.md`](../agents.local.md) §4
- Locked decisions: [`agents.local.md`](../agents.local.md) §2
- Troubleshooting: [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)