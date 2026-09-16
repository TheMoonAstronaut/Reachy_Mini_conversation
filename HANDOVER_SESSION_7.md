# SESSION 7 SUMMARY — 交接文档(新对话开场用)

> 给**下一段新对话的 AI 助手**的快速上下文。
> 配合 `agents.local.md` + `plan.md` + `SPEC_V2.md` + `HANDOVER_SESSION_6.md` 使用。

---

## TL;DR(一句话)

本 session 完成了 **P0-1 浏览器语音自动播放(WebAudio 解锁)+ P0-2 语音双通道调通(录音/免提)+ 事故级修复(重复 daemon sleep 真机电机)+ VAD 自适应底噪 + real 语音工具伪调用修复 + 官方 BreathingMove 空闲待机移植 + TTS 响度/语速修复**;**遗留:TTS 播放的音量/语速用户实测仍不正常(排查档案见 §四,修复可能没打在用户实际听的链路上)**。**pytest 251 全绿,本轮全部改动未 commit。**

---

## 一、本 session 完成的事

### P0-1:浏览器语音播报自动播放 ✅(用户验收通过"自动说话已经可以了")

- 根因:浏览器 autoplay policy 拦截 `<audio autoplay>`(gr.Audio)
- 方案(WebAudio 一次性解锁):
  - 后端 `utils/camera_stream.py`:`GET /api/tts_state`(轮询:`available/should_play/path/mtime`;`should_play=(run_mode=="pure_sim")` 防 real 模式浏览器+真机扬声器双重发声)+ `GET /api/tts_audio`(FileResponse,no-store)
  - 前端新文件 `static/js/tts_autoplay.js`:首个用户手势 resume() AudioContext 解锁 → 500ms 轮询 `(path,mtime)` 变化 → decodeAudioData+BufferSource 播放;重叠掐旧播新;基线机制(加载前已存在音频不播)
  - `web_ui.py`:pill 状态徽章(🔇/🔊/▶,文字+图标双语义 WCAG)+ js_on_load 注入(版本号 `_TTS_AUTOPLAY_JS_VERSION` 防强缓存);`tts_player` 保留作手动重播
  - 测试 `tests/test_tts_autoplay_api.py` 9 用例

### P0-2:语音双通道调通 ✅(对话功能用户确认"基本上没有问题了")

- **录音模式静音根修**:系统默认输入源是哑的 `Reachy Mini Camera`(相机麦,rms=2)→ `pactl set-default-source` 切到板载声卡(rms=1341)。⚠️ 运行时改动,重启机器会丢(见 §三)
- **免提流式链路**:playwright fake-mic E2E(`tools/e2e_voice_stream_check.py`)证明 Gradio 6 streaming 机制正常;用户侧失败主因是上述哑默认源
- **VAD 自适应底噪**(`voice_loop.py`):实测环境底噪 rms=0.024 > 原固定阈值 0.015 → VAD 永远"说话中"→12s 超长截断循环送底噪给 ASR → 空结果。改为:冷启动 1.5s 学习期 + SILENCE 态 EMA 跟踪,阈值=EMA×2.5 钳 [0.008,0.10];显式传 `rms_threshold` 保持固定(旧测试兼容)。顺手修了 **EMA 死锁**(底噪被判语音→语句态不学→永远学不到)。测试 `test_voice_loop_adaptive.py` 7 用例
- **real+voice 文本回写**:RealVoiceLoop 轮次写 bus(`voice_turn_seq/voice_turn`)→ web_ui 1s Timer 消费 append 到 chatbot(🎤 前缀)
- 诊断埋点:`on_mic_stop` 收录音 sr/shape、pcm rms(近静音警告)、存盘 `/tmp/last_mic_input.wav`;`on_voice_stream` 首个 chunk;`chat_mode` 切换
- ASR 环回验证工具 `tools/debug_asr_loopback.py`(edge-tts→pcm→run_audio,识别一字不差 → 链路无罪)

### 事故级修复:重复 daemon 导致真机电机被睡眠 ✅(最高优先回归保护)

- **现象**:用户点语音按钮时"真实 reachymini 挂掉关闭"
- **根因链**:残留 daemon(state="error" 电机通信故障)占 8001 → `_probe_ready()` 要求 state=="running" 探测失败 → ModeManager 误判无健康 daemon → 新起第二个 daemon → 它打开 USB 串口成功、wake_up 电机、bind 8001 失败 → **shutdown 钩子执行 "Putting Reachy Mini to sleep" → 电机断电**
- **修复**(`mode_manager.py`):`start()` 新增 `_find_occupant_pid()`(psutil)+ `_reap_pid()`(TERM→KILL→等端口释放)——端口被占但不健康时先清理再新起
- 测试 `tests/test_real_daemon_runner.py` 5 用例(僵尸清理/健康收养/空闲新起/幂等/收养不杀)

### real 语音工具伪调用修复 ✅

- **根因**:`app.py` 的 `RealVoiceLoop(get_pipeline())` 共享 pipeline 从未注入 `tool_deps` → LLM 无工具可调,把"调用 dance 工具"当文本念出来
- **修复**:`web_ui.get_tool_deps_global()` 公共通道 + app.py 构造时注入(与文本/录音路径共享镜像 deps)
- 测试 `test_tool_deps_injection.py`

### TTS 文本清洗 ✅(下划线不再被念出来)

- 新 `utils/text_clean.py`:`clean_text_for_tts()` 去 Markdown 结构符(`**`/`__`/`~~`/`#`/列表符/链接 URL),残留裸 `_`→空格;单点接在 `voice_pipeline._speak`(覆盖文本/录音/免提/真机全部路径);聊天窗展示原文不变
- 测试 `test_text_clean.py` 12 用例

### 音量滑条 ✅(浏览器侧)

- `tts_autoplay.js` 播放链插 GainNode,0–200%,localStorage 持久化(key `reachy.ttsVolume`);pill 旁 🔉 滑条

### 空闲待机呼吸 ✅(官方移植,用户点名的"无命令时头部微动+天线摆动")

- **调研**:GitHub `pollen-robotics/reachy_mini_conversation_app` 源码(git clone 通,web fetch 超时)
  - 官方 `moves.py` `BreathingMove`:z ±5mm @0.1Hz 呼吸 + 天线 ±15° @0.5Hz **反向** + 1s 线性插值进入(防跳变)+ neutral antennas ±10°(防舵机抖动)+ duration=inf
  - 官方调度:60Hz 控制循环,无活动 delay 后启动,新 move 打断
- **移植**(`idle_breath.py`):参数照抄;duration 改 8s 有限段;`IdleBreathController` 后台线程 2s 轮询 bus 活动字段(last_reply/voice_turn_seq/last_tool_calls/chat_mode/run_mode),**空闲 25s 自动进入**;忙碌状态(listening/thinking/speaking/playing)不播;打断延迟 ≤8s
- **坑**:bus `status` 初始值是 `"starting"` 非 `"idle"`——白名单判断会永不启动,改忙碌否定集合
- app.py 2.9 节启动,镜像到 sim+real 双实例
- 另有 `tools/idle_sway.py` 手动工具(关键词:待机/休息/待命;"不动"保留 idle_do_nothing)
- 测试 `test_idle_breath.py` 9 + `test_idle_sway.py` 若干用例

---

## 二、当前系统状态

```
启动:./scripts/start.sh --ui        ✅(日志:stdout/stderr → 被 start.sh 重定向;见 §三.9)
浏览器:http://localhost:7860        ✅(硬刷新 Ctrl+Shift+R 拿新 js)
语音:录音模式 ✅ / 免提(real)✅ / 免提(sim 浏览器)✅(机制验证过)
TTS 播报:浏览器自动播放 ✅ / 真机扬声器链路存在 ⚠️音量语速问题(§四)
空闲呼吸:25s 无活动自动进入        ✅(服务日志应有 "[idle-breath] 已启动")
测试基线:pytest 251 passed / node 19 全过
```

---

## 三、⚠️ 关键警示(本期新增 + 沿袭)

**沿袭 HANDOVER_6 §三全部**(SDK 补丁、unixfd 插件、代理变量、模型 ID、viewer 版本号、daemon B 独立进程组、storage.googleapis.com 白名单、真机电源)。新增:

1. **pactl/amixer 是运行时改动,重启机器会丢**:
   - `pactl set-default-source alsa_input.pci-0000_00_1f.3.analog-stereo`(默认输入→板载活麦;原默认是哑的相机麦!)
   - `amixer -c 3 sset 'PCM',1 60`(真机扬声器 -20dB→0dB)
   - 持久化方案:写进 `scripts/start.sh` 或 systemd user unit(未做)
2. **edge-tts `--write-media` 输出实为 MP3**(24kHz)但文件名 `.wav`——下游一律按内容解码,别被后缀骗;`scipy.io.wavfile` 读不了(用 soundfile/ffmpeg)
3. **SDK 播放管线固定 16kHz**:`audio_base.py:116 SAMPLE_RATE=16000`,`push_audio_sample(data)` 不携带采样率
4. **`ffmpeg loudnorm` 滤镜会升采样率**(48k/192k)——用必须钉 `-ar`(本次钉 24000)
5. **bus `status` 初始值 `"starting"`** 非 `"idle"`(state_bus.py:36)——空闲判定要用忙碌否定集合
6. **EnergyVAD 显式传 `rms_threshold` = 固定阈值无学习期;不传 = 自适应**——RealVoiceLoop 生产默认自适应,测试注入固定阈值保时序
7. **`_TTS_AUTOPLAY_JS_VERSION`**(web_ui.py):改 `tts_autoplay.js` 后必须 bump(同 three_viewer 教训)
8. **`/tmp/reachy-app.log` 不是 app.py 写的**——basicConfig 只写 stderr,日志靠 start.sh 重定向;手动起的进程日志在启动时指定的文件(本 session 用 `/tmp/reachy-start-p01.log`)
9. **TTS `Played` 日志是 DEBUG 级**——INFO 日志里看不到,排查播放链路时先提级别或看 `[TTS] Generated`

---

## 四、🔴 遗留 P0:TTS 播放音量/语速仍不正常(用户实测,本 session 修复未生效)

### 用户体感

"声音还是非常非常小,并且说话的语速好像也被降低了"——**响度归一化(mean -21.3→-14.9dB 实测)+ play() 16k 重采样修复后,用户复测仍不正常**。

### 已确认事实(证据齐)

| # | 事实 | 证据 |
|---|---|---|
| 1 | SDK 播放管线固定按 16kHz 解释 push 的数据 | `audio_base.py:116` |
| 2 | edge-tts 输出 MP3@24kHz(原名 .wav) | ffprobe `\xff\xf3` 帧头 |
| 3 | loudnorm 曾把文件升 48kHz(已修 -ar 24000) | ffprobe |
| 4 | `EdgeTTS.play()` 原不重采样,24k 按 16k 播=慢 1.5x;已修(重采样到 16k,3 测试) | `edge_tts.py` |
| 5 | 归一化后音源 mean -14.9dB / max -1.7dB | volumedetect 实测 |
| 6 | 真机扬声器 alsa 已全 100%(0dB) | amixer |
| 7 | `[TTS] Played` 日志是 DEBUG 级,INFO 不可见 | edge_tts.py:103 |

### 关键未知(下轮第一件事!)

**用户听到的声音到底来自哪条链路?** 三条链路行为完全不同:

| 链路 | 路径 | 采样率敏感性 |
|---|---|---|
| A. 浏览器 WebAudio 自动播放 | `/api/tts_audio` → decodeAudioData → AudioContext | **不敏感**(自动按 buffer 采样率播) |
| B. 浏览器 gr.Audio tts_player | respond() outputs → `<audio>` 元素 | 不敏感 |
| C. 真机扬声器 | `_speak` → `play()` → push_audio_fn → GStreamerAudio 16k | **敏感**(已修重采样) |

→ **必须先问用户:什么模式下、声音从哪里出来?** 如果用户在 sim 模式听浏览器,那语速/音量问题与 C 链路的修复无关——嫌疑转向 WebAudio 播放或**双重播放**(A+B 同时响,听感"回声/怪异"可能被描述为"语速不正常"!`tts_player` 仍在 outputs 里,用户手势解锁后 autoplay 可能生效 → 与 WebAudio 双响)

### 下轮排查步骤(按序)

1. 问清链路:模式(sim/real)+ 声音出处(电脑音箱/真机)
2. `ffplay /tmp/$(ls -t /tmp/tmp*.wav | head -1)` 直接听音源——音源正常则问题在播放端
3. **强候选方案:改用 SDK `media.play_sound(path)` 替代 `push_audio_sample`**——`audio_gstreamer.py:522` 文件级播放,playbin 管线**自动重采样/格式转换**,从根上免疫采样率问题(且支持 mp3 直接播,省掉 soundfile 读取)。注意:play_sound 在 `media` 对象上(GStreamerAudio 或 real_mini.media),且有 head_wobbler tee 联动
4. 检查双重播放:浏览器 DevTools 看 `<audio>` 元素是否也在响;如是,把 tts_player 的 autoplay 关掉或从 outputs 摘除(只留 WebAudio 一路)
5. edge-tts CLI 自带 `--rate=+X% --volume=+X%` 参数——合成层调语速/音量,比 ffmpeg 干净(若音源本身要更快更响)
6. 浏览器侧如确有变速:查 `tts_autoplay.js` 的 AudioContext.sampleRate 与 buffer 采样率(理论上 WebAudio 自动 resample,但值得 console 打印确认)

---

## 五、未 commit 改动(本轮全部,建议尽快 commit)

```
修改: app.py / brain/doubao_brain.py / mode_manager.py / real_voice.py
      tts/edge_tts.py / utils/camera_stream.py / voice_loop.py
      voice_pipeline.py / web_ui.py / tests/test_real_voice.py / tools/core_tools.py
新增: idle_breath.py / utils/text_clean.py / static/js/tts_autoplay.js
      tools/idle_sway.py / tools/debug_asr_loopback.py / tools/e2e_voice_stream_check.py
      tests/ ×8(test_tts_autoplay_api 9, test_text_clean 12, test_real_daemon_runner 5,
      test_voice_loop_adaptive 7, test_idle_sway, test_tts_loudnorm, test_tool_deps_injection 2,
      test_idle_breath 9)
```

建议拆分(对应上次报给用户的 6 笔):

```
fix(V2): real daemon 僵尸清理 — 防 shutdown 睡眠真机电机         [事故级,最先提]
feat(P0-1): WebAudio TTS 自动播放 + 音量滑条 + 文本清洗
fix(P0-2): VAD 自适应底噪 + real+voice 轮次回写 chatbot + 语音埋点
fix: tool_deps 注入(real 语音伪调用)+ TTS 响度 loudnorm + 采样率归一
feat: idle_breath 空闲呼吸(官方 BreathingMove 移植)+ idle_sway 工具
chore: ASR 环回 + fake-mic E2E 排错工具
```

---

## 六、下轮新对话开场模板

```
我要继续推进 /home/seeed/Reachy_Mini_conversation。
请按顺序读:agents.local.md → HANDOVER_SESSION_7.md(最新)→ HANDOVER_SESSION_6.md。

当前任务:HANDOVER_SESSION_7 §四 —— TTS 播放音量/语速仍不正常。
先按 §四"排查步骤"第 1 条问我:什么模式、声音从哪里出来(电脑音箱还是真机),
然后按 2-6 推进。强候选:改用 SDK media.play_sound() 文件级播放。

环境:./scripts/start.sh --ui 起;真机先开电机电源再切「真机+仿真(有线)」。
测试基线 pytest 251 全绿。注意 §三 的 pactl/amixer 运行时改动重启会丢。
```
