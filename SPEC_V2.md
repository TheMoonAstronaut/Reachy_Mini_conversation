# V2 SPEC — 运行模式切换 + 语音对话

> 状态：已与用户对齐(2025-09-15)。Q1=有线+无线都适配(手头有线);Q3=免提连续对话;Q4=真机扬声器。
> 本 spec 落地 F1(real 模式实装)+ 新增对话模式切换。

---

## 需求原文

1. 浏览器下拉切换:**纯 sim 模式** ↔ **真实 reachymini 模式**(真机交流的同时 sim 一比一映射动作)
2. 按钮切换**文本对话 ↔ 语音对话**;语音音源:real 用 Reachy Mini 自己的麦克风,sim 用电脑麦克风

## 架构决策(已定)

### D1. 双 daemon 双 client(有线版)

```
┌─ daemon A: sim    @localhost:8000(常驻,现状不变)
│     ↑ sim_mini = ReachyMini(port=8000, connection_mode="localhost_only")
│
└─ daemon B: real   @localhost:8001(切到 real 模式时才起,--serialport auto)
      ↑ real_mini = ReachyMini(port=8001, connection_mode="localhost_only")
```

- daemon B **不带** `--sim` → 真机 backend;`--serialport auto` 自动找 USB 串口
- daemon B **不带媒体**(`--no-media`):避免 UDP 5005/5006 与 daemon A 的 GStreamer 冲突。
  真机摄像头本期不接(副视角 real 模式占位);真机麦克风 **不走 daemon 媒体链**,
  走 daemon B 的音频设备(SDK MediaManager 直接读 USB 声卡)——实测点 R1 验证。
- 无线版:不起 daemon B,`real_mini = ReachyMini(connection_mode="network")`
  连机器人自带 daemon(mDNS `reachy-mini.local` 或 env.json 配 host)

### D2. env.json 扩展(向后兼容,缺省 wired)

```json
"real_connection": { "type": "wired", "serial_port": "auto", "port": 8001 }
"real_connection": { "type": "wireless", "host": "reachy-mini.local", "port": 8000 }
```

### D3. 运行时切换(MirrorOrchestrator 加插拔)

- `orchestrator.attach_real(mini)` / `detach_real()` — 线程安全插拔
- 切换流程(切到 real):起 daemon B(wired)→ 建 client → 健康检查
  (get_status 3s)→ 成功:attach + bus.run_mode 更新;失败:杀 daemon B、
  回滚 pure_sim、bus.error 提示、UI 徽章变红
- 切回 pure_sim:detach + 杀 daemon B(有线)/ 断 client(无线)

### D4. 语音模式(免提连续对话)

- UI:对话面板顶部 💬/🎤 切换(gra Radio 或 Toggle)
- **sim 音源**:浏览器麦克风。方案:复用现有 `gr.Audio` 录音组件,语音模式下
  自动循环(识别完一轮自动开下一轮),即"准连续";真流式(streaming chunk)
  留 V2.x 增强。**理由**:gr.Audio 分轮录音已验证可用,循环化即可达成
  "说完自动识别→播报→继续听"的免提体验,风险最低。
- **real 音源**:后台线程 `real_mini.media.get_audio_sample()` 循环读 +
  能量 VAD 分句(决策 15,沿用 main.py 简单阈值)→ run_audio()
- **TTS 输出**:real 模式 `real_mini.media.push_audio_sample()` 推真机扬声器;
  sim 模式维持浏览器播报(现状)
- **打断(barge-in)**:本期不做(TTS 播放中不抢麦),留后续

### D5. UI 变更

- 顶栏 pill 区加「运行模式」下拉:`🧪 纯仿真` / `🤖 真机+仿真(有线)` / `🌐 真机+仿真(无线)`
  (下拉里直接区分有线/无线,省去设置面板来回)
- 切换中徽章 🟠「连接真机中…」;失败 🔴「真机连接失败」3s 后回滚显示
- real 模式下:副视角显示"真机摄像头未接(本期)"占位;主区 3D 视图
  跟随 sim(sim 永远在线,天然一比一映射的显示端)

## 切片计划

| 切片 | 内容 | 验收 | 依赖真机? |
|---|---|---|---|
| V2.1 | ModeManager + orchestrator 插拔 + 单测 | 假 mini 单测全绿 | 否 |
| V2.2 | UI 下拉 + app 集成 + 失败回滚 | 无真机时切 real → 优雅报错回滚 | 否(测失败路径) |
| V2.3 | 语音模式(sim 侧:浏览器麦循环连续对话) | 浏览器实测连续两轮语音 | 否 |
| V2.4 | real 侧:daemon B 起停 + 真机麦 + TTS 推真机 | 插真机实测 | **是** |
| V2.5 | 一比一映射实测(真机动=sim 动) | 插真机实测 | **是** |

## 实测点(需真机时验证)

- R1:daemon B `--no-media` 下 `real_mini.media.get_audio_sample()` 是否可用
  (媒体链禁用≠音频设备禁用,SDK 音频走 sounddevice 独立通道;若不可用,
  备选:daemon B 不带 --no-media 但需解决 UDP 端口冲突)
- R2:有线版 USB 声卡识别(Reachy Mini Audio USB)
- R3:双 daemon 资源占用(CPU/内存,笔记本风扇)

## 明确不做(本期)

- 真机摄像头接入(副视角 real 模式占位)
- 打断/抢话(barge-in)
- 无线版实测(无设备;代码路径留好)
- 多真机
