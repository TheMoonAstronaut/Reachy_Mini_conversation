# ROBOT.md — 部署到机器人本体(无线版树莓派)/ On-Robot Deployment

> 适用:Reachy Mini **无线版**(机身内置 Raspberry Pi CM4,4GB RAM,WiFi)。
> 效果:项目直接跑在机器人树莓派上,同一 WiFi 下任意设备用浏览器打开
> UI 控制机器人,无需 PC 常驻、无需 USB 线。

---

## 架构说明

无线版树莓派上**本来就运行着官方 daemon**(电机 + 相机 + 麦克风 + 扬声器,
端口 8000,系统服务托管)。on-robot 模式 = 本项目以 `pure_real` 模式直接
连这个本体 daemon:

```
手机/平板/电脑浏览器 ──WiFi──▶ 本项目(on 树莓派,7860)──localhost──▶ 官方 daemon(8000)
                                                                      ├─ 电机(serial)
                                                                      ├─ 相机(USB/CSI)
                                                                      ├─ 麦克风阵列
                                                                      └─ 扬声器
```

- **不启动 Mujoco 仿真**(CM4 算力跑不动物理仿真,也不需要——机器人是真的)
- **3D 视图保留**:three.js 在浏览器渲染,树莓派只广播 25Hz 关节数据
- 语音对话 / 手部跟随 / 待机微动 / 工具动作 全部可用(MediaPipe 在
  CM4 上手部跟随约 5-8fps,比 PC 慢但可用)

## 步骤 1:SSH 上树莓派

```bash
ssh pollen@reachy-mini.local
# 密码: root(官方默认,见官方文档 get_started)
```

## 步骤 2:安装系统依赖

树莓派官方系统是 Raspberry Pi OS(Debian Bookworm):

```bash
sudo apt update
sudo apt install -y \
  libcairo2-dev libgirepository1.0-dev pkg-config python3-dev \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  ffmpeg git python3-venv
```

> CM4 上没有 conda 也没关系,用 venv 即可(Python ≥ 3.10,Bookworm 自带 3.11)。

## 步骤 3:部署项目

```bash
git clone https://github.com/TheMoonAstronaut/Reachy_Mini_conversation.git
cd Reachy_Mini_conversation
python3 -m venv ~/.venv/reachy
source ~/.venv/reachy/bin/activate
pip install -e ".[dev]"
```

依赖在 aarch64 上的可用性(2026-09-18 核验):mediapipe 有官方
`manylinux_2_28_aarch64` 轮子;mujoco 仅纯仿真模式需要(on-robot 不启动
sim,装了也不运行)。若 `pip install` 在个别包上卡住,先 `pip install`
其余包并把问题提到仓库 Issues。

## 步骤 4:配置 API Key

与 PC 部署相同,写到树莓派上的 `~/.reachymini/env.json`:

```bash
mkdir -p ~/.reachymini
cat > ~/.reachymini/env.json <<'EOF'
{
  "doubao_llm": { "api_key": "your-ark-api-key", "model": "doubao-seed-character-251128" },
  "doubao_asr": { "api_key": "your-asr-api-key" },
  "edge_tts":   { "voice": "zh-CN-XiaoxiaoNeural" }
}
EOF
chmod 600 ~/.reachymini/env.json
```

> 需要机器人能访问互联网(豆包/edge-tts 都是云端 API)。

## 步骤 5:(可选)下载手部跟随模型

```bash
mkdir -p ~/.cache/reachymini
curl -L -o ~/.cache/reachymini/hand_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task
```

## 步骤 6:启动

```bash
source ~/.venv/reachy/bin/activate
./scripts/start.sh --robot
```

`--robot` 做的事:
1. 检查本体官方 daemon(:8000)就绪——**不要停掉它**,它是电机/媒体的唯一通道
2. 设置 `REACHYMINI_RUN_MODE=pure_real`,跳过 sim daemon 启动
3. 启动应用(Gradio @ 7860),横幅打印局域网链接

然后同一 WiFi 的任意设备浏览器打开 `http://<机器人IP>:7860`
(启动横幅会打印;机器人 IP 可在路由器设备列表或 `hostname -I` 查看)。

## 麦克风排障(重要)

若机器人自检 / arecord 录音为**纯零**(播放正常),这是无线版最常见的
硬件坑,官方 Troubleshooting 首位原因:**麦克风 FPC 排线插反**
(外观看不出,需拔下翻面重插;彻底断电再上电)。其次:FPC 线损坏
(官方有换线教程)、固件 < 2.1.4(跑官方 `assets/firmware/update.sh`)。

快速自检:
```bash
arecord -D reachymini_audio_src -f S16_LE -r 16000 -c 2 -d 4 /tmp/t.wav
python3 -c "import soundfile as sf, numpy as np; d,_=sf.read('/tmp/t.wav'); print(np.abs(d).max())"
# 输出 0.0 = 麦克风链路无数据 → 按上面三步走
```

## 已知边界

| 项 | on-robot 状态 |
|---|---|
| 电机控制 / 工具动作 / 待机微动 | ✓ 全速 |
| 语音对话(ASR/LLM/TTS) | ✓ 全速(网络 API) |
| 3D 视图 | ✓(浏览器渲染,零本体成本) |
| 相机画面(`/camera_feed`) | ✓ 依赖相机作为 USB 设备可见(v4l2 by-id 含 "Reachy")|
| 手部跟随 | 🟡 MediaPipe 在 CM4 上 ~5-8fps,跟随偏钝但可用 |
| MuJoCo 仿真画面 | ✗ 本体模式无 sim(UI 对应区域显示占位) |
| 运行时切换纯仿真/有线 | ✗ 下拉已禁用(on-robot 不支持切模式) |

## 性能提示

- CM4 是 4 核 A72 / 4GB,不要同时开多个浏览器视频连接(每个 MJPEG
  连接都有解码/带宽成本)
- 手部跟随不用时记得关(顶部徽章或对话「停止手部跟随」),MediaPipe
  持续占 1-1.5 核
- 树莓派供电不足会导致 WiFi 抖动;尽量用原装电源并静置充电
