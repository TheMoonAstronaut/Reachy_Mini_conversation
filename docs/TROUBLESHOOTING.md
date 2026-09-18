# TROUBLESHOOTING.md — 故障排查 / Troubleshooting Guide

> 配套文档:[`INSTALL.md`](INSTALL.md) / [`CONFIG.md`](CONFIG.md)
> 安装问题先看 [`INSTALL.md`](INSTALL.md)

---

## 中文

### 1. conda 装不上 / 创建环境失败

#### 症状

```
ResolvePackageNotFoundError:
  - funasr
```

或 `environment.yml` 里某个包 conda 找不到。

#### 原因

`environment.yml` 里有些包(decision 13 变更后已无 funasr)是 pip 段装的,但其他段可能在 conda 索引里没有。

#### 解决

```bash
# 选项 A:更新 conda 索引
conda update -n base -c defaults conda
conda env update -f environment.yml

# 选项 B:用 mamba 加速(更快、更稳)
conda install -n base -c conda-forge mamba
mamba env create -f environment.yml
```

### 2. GStreamer 缺失 / Mujoco 视频流端口被占

#### 症状

启动 daemon 报:
```
ERROR: Failed to bind GStreamer pipeline on UDP port 5005
```
或:
```
OSError: [Errno 98] Address already in use
```

#### 解决

```bash
# 1. 看占用 5005 端口的进程
lsof -i :5005
# 或
ss -tulnp | grep 5005

# 2. 杀掉它
kill -9 <PID>

# 3. 重启 daemon
./scripts/start.sh
```

如果 GStreamer 根本不存在(全新系统):
```bash
sudo ./scripts/install_deps.sh  # 重新跑装系统依赖
gst-inspect-1.0 --version      # 验证
```

### 3. 豆包 ASR 连接失败 / API Key 错误

#### 症状

启动时 WARNING:
```
ASR api_key 未配置(豆包 WebSocket 流式,需要 .reachymini/env.json 填 doubao_asr.api_key)
```

运行时报:
```
websockets.exceptions.WebSocketException: ...
401 Unauthorized
```
或:
```
连接 openspeech.bytedance.com:443 超时
```

#### 解决

```bash
# 1. 验证 API Key 已填
cat ~/.reachymini/env.json

# 2. 验证文件权限(应该 0600)
ls -la ~/.reachymini/env.json

# 3. 验证 Key 在火山引擎控制台是否有效
# https://www.volcengine.com/product/asr

# 4. 验证网络可达性
curl -v https://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream 2>&1 | head -10
```

#### 防火墙 / 代理

如果你在公司网络:
- 确认 `openspeech.bytedance.com:443` 不被拦截
- 配置 HTTPS 代理:`export HTTPS_PROXY=http://proxy.example.com:8080`

### 4. 豆包 LLM 401 / 配额不足

#### 症状

```
openai.AuthenticationError: 401 Incorrect API key provided
```
或:
```
429 Rate limit exceeded
```

#### 解决

1. **API Key 错**:重新从方舟控制台复制
2. **模型 ID 错**:`doubao-seed-character-251128` 需要你在方舟控制台**开通了**这个模型,否则会 404
3. **余额不足**:充值
4. **配额限速**:升级或等几分钟

### 5. Edge TTS 没有声音

#### 症状

TTS 调用成功但没声音 / `edge-tts` 报 `NoAudioReceived`。

#### 原因

edge-tts 是免费 API,但需要网络访问 `speech.platform.bing.com`(白名单 §4)。部分 IP 段被微软封了。

#### 解决

```bash
# 1. 验证网络
curl -v https://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1 2>&1 | head -5

# 2. 换个 voice 试试
# 编辑 ~/.reachymini/env.json,把 voice 改成 zh-CN-YunxiNeural

# 3. 临时开 proxy
export HTTPS_PROXY=...
```

### 6. Mujoco 仿真启动慢 / 卡住

#### 症状

启动 daemon 后,Mujoco 窗口要 30 秒才出来,或直接卡死。

#### 解决

```bash
# 1. 检查 GPU 驱动(虽然 CPU-only 也跑,有些 GPU 驱动问题会卡 GStreamer)
nvidia-smi  # 看你有没有 NVIDIA GPU

# 2. 检查 X11 / Wayland 显示
echo $DISPLAY

# 3. 强制 headless(无显示)
./scripts/start.sh --headless
```

### 7. MediaPipe 1.0+ import 错

#### 症状

```
ImportError: cannot import name 'solutions' from 'mediapipe'
```

#### 原因

mediapipe 1.0+ 删了旧的 `solutions` API,统一改用 `tasks` API。`plan.md §4.3` 写的是旧 API,P6 实装 HandFollower 时会改成新 API。

#### 当前 P0 状态

`tests/smoke_test.py::test_mediapipe_new_api` 验证新 API 可用,不需要修。

P6 实装时改 `plan.md §4.3` 用 `vision.HandLandmarker`。

### 8. ReSpeaker XVF3800 不识别 / 固件要升级

#### 症状

`AudioDoA` 报设备找不到,或 `get_DoA()` 一直返回 None。

#### 解决

1. 确认 USB 设备在:`lsusb | grep ReSpeaker`
2. 装 seeed 官方驱动:https://wiki.seeedstudio.com/cn/respeaker_xvf3800_introduction/
3. 固件升级:https://wiki.seeedstudio.com/cn/respeaker_xvf3800_firmware/
4. 测试:
   ```python
   import sounddevice as sd
   print(sd.query_devices())
   ```

#### 仿真模式

如果没真机 ReSpeaker,`SoundLocalizer` P5 实装时会降级:UI 显示 "N/A",不阻塞其他功能。

### 9. 启动报 "numpy >= 2.2.5 required"

#### 症状

```
reachy_mini 要求 numpy >= 2.2.5,但装的是 numpy 1.x
```

#### 原因

`reachy_mini 1.10.0` 的硬要求。funasr 会强制 numpy 1.x(已在 P0 决策 13 变更后移除)。

#### 解决

```bash
# 检查 numpy 版本
python -c "import numpy; print(numpy.__version__)"

# 强制升 2.x
pip install --upgrade 'numpy>=2.2.5'

# 如果装 funasr / funasr-onnx 又降到 1.x(不应该)
# 应该是没装这俩包,如果装了卸掉
pip uninstall -y funasr funasr-onnx
```

### 10. Python 版本不对

#### 症状

```
ERROR: Package 'reachy-mini' requires a different Python: 3.10.0 not in '>=3.11'
```

#### 解决

```bash
# conda 环境必须用 Python 3.11+,我们默认 3.12
conda env create -f environment.yml  # 用 environment.yml 里的 python=3.12
```

### 11. `reachy-mini-conversation: 未找到命令`

#### 症状

CLI 找不到。

#### 解决

```bash
# 1. 确认 conda 环境激活了
conda activate reachy

# 2. 重新安装
pip install -e .

# 3. 验证 wrapper 存在
which reachy-mini-conversation
# 应该输出 /home/<user>/miniforge3/envs/reachy/bin/reachy-mini-conversation
```

### 12. daemon 起不来 / 报端口冲突

#### 症状

`reachy-mini-daemon` 启动失败,报端口 8000 被占。

#### 解决

```bash
lsof -i :8000
kill -9 <PID>
```

### 13. 完整 reset(全部清空重来)

```bash
# 1. 关掉所有相关进程
pkill -f reachy-mini-daemon
pkill -f reachy-mini-conversation

# 2. 删 conda 环境
conda env remove -n reachy

# 3. 删用户配置(API Key + 数据集缓存)
rm -rf ~/.reachymini
rm -rf ~/.cache/huggingface/datasets/pollen-robotics*
rm -rf ~/.cache/modelscope  # funasr 用,我们已不用,但保险清掉

# 4. 重新安装(参考 INSTALL.md)
conda env create -f environment.yml
conda activate reachy
pip install -e ".[dev]"
```

### 14. `ValueError: Unknown scheme for proxy URL URL('socks://...')`

#### 症状

调用 LLM/ASR 或任何 httpx 客户端时崩:
```
ValueError: Unknown scheme for proxy URL URL('socks://127.0.0.1:xxxx')
```

#### 原因

桌面环境残留了 socks 协议的代理环境变量(`https_proxy=socks://...`)。
httpx 不支持 socks scheme,直接抛错。

#### 解决

6 个变量(大小写两套)全部清掉:
```bash
unset HTTPS_PROXY HTTP_PROXY ALL_PROXY https_proxy http_proxy all_proxy
```

`scripts/start.sh` 开头已内置此清理——用脚本启动即可免疫。
手工跑 `python -m reachymini_conversation --ui` 前则需自己执行上面的 unset。

### 15. sim 视频流黑屏 / UI 显示占位图

#### 症状

UI 视频区显示占位图,`curl -s http://localhost:7861/sim_feed_status`
返回 `available=false`、`frames_received=0`。

#### 排查路径(按顺序)

```bash
# 1. 确认 daemon 带媒体启动(不是 --no-media)
tail -50 /tmp/reachy-daemon.log | grep -iE "media|udp|error"

# 2. 检查 GStreamer 插件是否齐全
gst-inspect-1.0 unixfdsink   # daemon → 本地客户端 IPC(GStreamer < 1.24 需 backport)
gst-inspect-1.0 unixfdsrc
gst-inspect-1.0 udpsrc       # UDP:5005 接收
gst-inspect-1.0 rtpvrawdepay
gst-inspect-1.0 webrtcsink   # 仅 WebRTC 远程流需要,缺失时本地链路仍可工作

# 或用内置 guard 一次查全:
python -c "from reachymini_conversation.utils.gst_plugins import check_sim_video_plugins; check_sim_video_plugins()"

# 3. 检查 EGL(Mujoco 离屏渲染,NVIDIA 必需)
MUJOCO_GL=egl python -c "
import mujoco; m = mujoco.MjModel.from_xml_string('<mujoco/>')
d = mujoco.MjData(m); r = mujoco.Renderer(m, 240, 320)
mujoco.mj_forward(m, d); r.update_scene(d); print('EGL OK', r.render().shape)"

# 4. 确认 daemon 的 IPC socket 存在(媒体服务器活着)
ls -la /tmp/reachymini_camera_socket

# 5. 驱动状态计数:先 curl 一次 /sim_feed(计数只在有客户端时驱动)
curl -s --max-time 3 -o /dev/null http://localhost:7861/sim_feed
curl -s http://localhost:7861/sim_feed_status   # frames_received 应 > 0 且增长
```

#### 常见根因与修复

- **`Failed to create webrtcsink element`**:gst-plugins-rs 未安装。
  有 sudo:`sudo apt install gstreamer1.0-plugins-rs`(Ubuntu ≥ 23.10);
  无 sudo / Ubuntu 22.04:SDK `media_server.py` 已打 P7.B 降级补丁
  (无 webrtcsink 时仅本地 IPC),不阻塞 sim 视频流。
- **`Failed to create unixfdsink element`**:unixfd 插件 1.24 才进官方源码树。
  本机(GStreamer 1.20)已用 backport 插件修复,位于
  `~/.local/share/gstreamer-1.0/plugins/libgstunixfd.so`;
  重装系统/换机时需重新构建(见本节上方的重建命令)。
- **EGL 不可用**:`ls /usr/share/glvnd/egl_vendor.d/` 应有 `10_nvidia.json`;
  确认 `libnvidia-egl-*` 已装。必要时启动脚本里 `export MUJOCO_GL=egl`。
- **daemon 的 central signaling relay 反复报 `Connect call failed ('127.0.0.1', 8443)`**:
  webrtcsink 缺失时信令服务器不存在,relay 重试属预期噪音,不影响本地视频链路。

---

## English

### 1. conda env creation fails

```bash
conda update -n base -c defaults conda
mamba env create -f environment.yml  # or use mamba for speed
```

### 2. GStreamer / Mujoco port 5005 in use

```bash
lsof -i :5005
kill -9 <PID>
./scripts/start.sh
```

### 3. Doubao ASR auth/connection fails

- Verify `~/.reachymini/env.json` has `doubao_asr.api_key`
- Test network: `curl -v https://openspeech.bytedance.com/...`
- Check corporate firewall

### 4. Doubao LLM 401/429

- Wrong API key → re-copy from console
- Wrong model ID → `doubao-seed-character-251128` must be enabled in your Ark console
- Out of quota → top up
- Rate limit → wait or upgrade

### 5. Edge TTS no audio

- Test network: `curl -v https://speech.platform.bing.com/...`
- Try a different voice
- Configure `HTTPS_PROXY` if behind firewall

### 6. Mujoco slow / stuck

```bash
nvidia-smi                 # check GPU
./scripts/start.sh --headless  # no display
```

### 7. MediaPipe API mismatch

mediapipe 1.0+ removed `solutions` API. P6 will use `vision.HandLandmarker`. Currently no action needed (P0 only verifies import).

### 8. ReSpeaker XVF3800 not detected

1. `lsusb | grep ReSpeaker`
2. Install seeed drivers + firmware (wiki.seeedstudio.com)
3. `python -c "import sounddevice; print(sounddevice.query_devices())"`

### 9. numpy version conflict

`reachy_mini` requires `numpy >= 2.2.5`. If something downgraded it, remove funasr/funasr-onnx (which we don't need) and `pip install --upgrade 'numpy>=2.2.5'`.

### 10. Python version wrong

Use Python 3.11+ (3.12 recommended). `environment.yml` specifies this.

### 11. `reachy-mini-conversation: command not found`

```bash
conda activate reachy
pip install -e .
```

### 12. daemon port 8000 in use

```bash
lsof -i :8000
kill -9 <PID>
```

### 13. Full reset

```bash
pkill -f reachy-mini-daemon
pkill -f reachy-mini-conversation
conda env remove -n reachy
rm -rf ~/.reachymini
rm -rf ~/.cache/huggingface/datasets/pollen-robotics*
rm -rf ~/.cache/modelscope
conda env create -f environment.yml
conda activate reachy
pip install -e ".[dev]"
```

### 14. `ValueError: Unknown scheme for proxy URL URL('socks://...')`

httpx does not support the `socks://` scheme. Leftover desktop proxy variables
crash any httpx-based call. Unset all six variables (both cases):

```bash
unset HTTPS_PROXY HTTP_PROXY ALL_PROXY https_proxy http_proxy all_proxy
```

`scripts/start.sh` already does this at the top — launching via the script is immune.

### 15. Sim video feed black / placeholder image in UI

`curl -s http://localhost:7861/sim_feed_status` shows `available=false`,
`frames_received=0`. Debug path:

```bash
# 1. daemon must run WITHOUT --no-media; check its log
tail -50 /tmp/reachy-daemon.log | grep -iE "media|udp|error"

# 2. check GStreamer elements (unixfd needs the backported plugin on GStreamer < 1.24)
gst-inspect-1.0 unixfdsink unixfdsrc udpsrc rtpvrawdepay webrtcsink

# 3. check EGL offscreen rendering (NVIDIA)
MUJOCO_GL=egl python -c "import mujoco; m=mujoco.MjModel.from_xml_string('<mujoco/>'); \
d=mujoco.MjData(m); r=mujoco.Renderer(m,240,320); mujoco.mj_forward(m,d); \
r.update_scene(d); print('EGL OK', r.render().shape)"

# 4. counters only advance while a client is connected — hit /sim_feed first
curl -s --max-time 3 -o /dev/null http://localhost:7861/sim_feed
curl -s http://localhost:7861/sim_feed_status
```

See the Chinese section for detailed root causes (webrtcsink P7.B degradation,
unixfd backport plugin, EGL vendor libs).

---

## Still stuck?

1. 看 [`agents.local.md`](../agents.local.md) §2 决策表确认你是否偏离了决策
2. 看 [`plan.md`](../plan.md) §3 阶段规划确认当前阶段该有的功能
3. 看 [`tests/smoke_test.py`](../tests/smoke_test.py) 跑 `pytest -v` 看哪些测试挂了
4. 提 issue(待 P8 完善)