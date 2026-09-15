# SESSION 6 SUMMARY — 交接文档(新对话开场用)

> 给**下一段新对话的 AI 助手**的快速上下文。
> 配合 `agents.local.md` + `plan.md` + `SPEC_V2.md` + `HANDOVER_SESSION_5.md` 使用。

---

## TL;DR(一句话)

本 session 完成了**两大特性 + 两轮真机实测 bug 修复**:① 浏览器内 three.js **真交互式 3D 视图**(鼠标旋转/平移/缩放,25Hz 实时跟随 daemon,顶点级运动学校验到 1e-10 m);② **运行模式切换(纯仿真↔真机)+ 语音对话框架**(双 daemon 架构、真机 USB 外设直连、免提 VAD 分句)。**遗留:浏览器语音播报不自动播放(autoplay 策略)+ 浏览器麦克风提取待实测 + 真机电机电源要开**。**pytest 192 全绿,未 commit。**

---

## 一、本 session 完成的事

### V1:three.js 交互式 3D 视图(方案 B 落地,HANDOVER_5 §六 P0 V1)

| 子任务 | 结果 | 证据 |
|---|---|---|
| V1.1 WS 状态通道 | ✅ | 7861 `/ws/state` @25Hz(实测 40.2ms 间隔),推 `head_joints[7]+antennas[2]+head_pose[16]` |
| V1.2 资源管线 | ✅ | `tools/export_visual_manifest.py` 解析 MJCF → `static/meshes/`(manifest.json + 41 STL,16MB);7861 StaticFiles 伺服 + CORS(7860) |
| V1.3 three.js 前端 | ✅ | `static/js/three_viewer.js`(vendor three.js r180 本地 2MB,零 CDN);OrbitControls 左键旋转/右键平移/滚轮缩放 |
| V1.4 Gradio 集成 | ✅ | `gr.HTML` + `js_on_load` 注入,`?v=` 版本号防缓存 |
| V1.5 布局 | ✅ | 主区 3D;场景 MJPEG 降级 Accordion 对照;眼睛 MJPEG 保留副视角 |

**运动学核心决策**(踩过的坑都在这):
- `head_joints = [yaw_body, stewart_1..6]`(actuator 序);`antennas = [right, left]` 且 **SDK 返回时取负**(`return -pos`)→ 前端 `rotation.z = -antennas[i]`
- `get_current_head_pose()` 返回 head site 世界位姿且 **z 被减了 0.177**(SDK 约定)→ 前端 `elements[14] += 0.177` 还原
- **MuJoCo hinge 规则**(数值验证):`body_world(θ) = parent_world · T(pos,quat) · R(axis,θ)` —— 旋转在局部系后乘
- **MuJoCo 编译期会居中+主轴对齐 mesh**(`mesh_pos/mesh_quat` 非零)→ `geom_xpos` 不是 raw STL 的摆放位姿!manifest 必须用 XML 原始 geom pos/quat 配 raw STL(顶点级豪斯多夫 8.6e-10 m 证明)
- Stewart 连杆(被动球关节):**两端球铰连线法**渲染,不解并联 FK(下端铰在 horn 系 (0.04,0,0.007),上端铰 = closing_i_2 site / xl_330 原点,杆长恒 0.085m)
- JS 与 Python/MuJoCo FK 对拍:`node tests/test_three_rig.mjs`(golden fixture `tests/golden_rig_fixture.json`),误差 ≤4e-6 m

### V2:运行模式切换 + 语音对话(SPEC_V2.md)

| 子任务 | 结果 |
|---|---|
| V2.1 ModeManager | ✅ `mode_manager.py`:orchestrator `attach_real/detach_real` 运行时插拔;失败回滚;孤儿 daemon B 收养逻辑;`wait_ready` 要求 `state=="running"` |
| V2.2 UI 下拉 | ✅ 顶栏 🧪纯仿真/🤖真机+仿真(有线)/🌐无线;🟠连接中徽章;真机实测 10s 连上 |
| V2.3 语音免提(sim) | ✅ `voice_loop.py` EnergyVAD(能量阈值+拖尾+前导保护+超长截断,7 单测);对话面板 💬/🎤 切换;流式麦克风 `gr.Audio(streaming=True)` |
| V2.4 语音免提(real) | ✅ `real_voice.py` RealVoiceLoop;**有线外设全部 app 直连本机 USB**:`local_audio.py`(SDK GStreamerAudio 声卡直连,已实测采到真机麦信号)+ `local_camera.py`(v4l2 MJPEG 直出,已实测出画面);TTS 路由 real→真机扬声器 |

### 两轮 bug 修复(真机实测驱动)

1. **跳舞只点头** → LLM 倾向显式传 `move='simple_nod'`。修:`tools/dance.py` 描述改"默认留空随机;点名舞种才传" + system prompt 精确规则(含"欢快/优雅形容词不算点名"、「点点头」固定走 `dance(simple_nod)` 防回归)。6 指令×3 轮全稳定
2. **声源跟随莫名启动** → 真机插入后 ReSpeaker 激活,P5 的 SoundLocalizer 是自动的。修:**默认关**,新「🧭 声源跟随」Accordion 显式开
3. **跳舞真机不动** → ToolDependencies 注入的是裸 sim_mini,工具不打真机。修:`MirroredToolTarget` 适配器镜像到双实例(实测跳舞真机关节动了)
4. **真机摄像头/麦克风无** → daemon B `--no-media` 全禁了(我的锅)。修:外设改 app 直连 USB(见上)
5. **声学反馈循环**(真机扬声器→麦克风→再回复→死循环乱动)→ TTS 播放期间麦克风静音(播放时长+0.4s)
6. **手势跟随无效** → `hand_landmarker.task` 从未下载。已下(7.8MB → `~/.cache/reachymini/`);网络白名单需登记 storage.googleapis.com(见 §三.7)
7. **logging 没配置** → app.py main() 加 basicConfig,现在 `/tmp/reachy-app.log` 全程可见

### 实测截图证据(在 /tmp,重启会丢)

- `/tmp/v1_shot_before.png`:3D 视图 rest 姿态(连杆可见)
- `/tmp/v1_dancing.png`:舞蹈中段(头倾斜+连杆跟随)
- `/tmp/v2_real_mode.png`:real 模式(徽章 🤖 + 副视角真机实拍房间画面)

---

## 二、当前系统状态

```
启动:./scripts/start.sh --ui            ✅ 一键
浏览器:http://localhost:7860            ✅(3D 主区 + 模式下拉 + 语音切换)
真机切换:顶栏下拉「真机+仿真(有线)」     ✅ 实测 10s(电机电源须开!)
跳舞镜像:真机+sim 同步                  ✅ 实测
真机摄像头:/camera_feed(v4l2 直连)     ✅ 实测出画面
真机麦克风:USB 声卡直连                  ✅ 实测采到音(ASR 识别出环境人声)
TTS→真机扬声器                          ✅ 链路实测跑通
测试基线:pytest 192 passed / node 19 全过
```

**重要:真机电机电源开关**——daemon B 日志若见 `No motors detected` 就是电源没开。

---

## 三、⚠️ 关键警示(本期新增 + 沿袭)

1. **SDK site-packages 补丁仍在**:`media_server.py`(P7.B)+ `gstreamer_udp_camera.py`(caps)。`pip install -U reachy_mini` 会丢
2. **自编译 unixfd 插件**:`~/.local/share/gstreamer-1.0/plugins/libgstunixfd.so`,重建 `tools/gst_unixfd_backport/build.sh`
3. **代理变量**:手动 python/curl 前必须 `unset HTTPS_PROXY HTTP_PROXY ALL_PROXY https_proxy http_proxy all_proxy`
4. **模型 ID**:`doubao-seed-character-251128`,勿用 2-1-pro 系(伪 function calling)
5. **(新)改 three_viewer.js 后 bump 版本号**:`web_ui.py` 的 `_VIEWER_JS_VERSION`(否则浏览器强缓存拿旧版)
6. **(新)daemon B 是独立进程组**(start_new_session):app 重启不杀它。ModeManager 起前会探测收养;手动清理:`ss -tlnp | grep 8001` 找 PID kill
7. **(新)网络白名单漏登记**:`storage.googleapis.com`(手势模型,F4 一次性下载,类比决策 16D)——需在 `agents.local.md` §4 补登记
8. **(新)真机电源注意**:USB 插着 ≠ 电机上电;切换报"No motors"先查电源开关

---

## 四、🔴 遗留问题(下轮 P0)

### P0-1:浏览器语音播报不自动播放(用户痛点原话:"我要的是 reachymini 能够自动的讲话,而不是我需要再点击播放")

**根因调研结论**:浏览器的 **autoplay policy**(Chrome:无用户手势激活时,`AudioContext` 处于 suspended/媒体元素被静音播放)。`gr.Audio(autoplay=True)` 生成的是 `<audio autoplay>`,在页面没有发生过用户交互前会被浏览器拦住。

**候选方案**(按推荐序):
- A. **WebAudio + 一次性解锁**:页面首个用户手势(任意点击)时 `new AudioContext().resume()`;TTS 改用 `AudioContext.decodeAudioData + BufferSource` 播放(不再依赖 `<audio autoplay>`)。Gradio 侧用 gr.HTML/js 注入一个小模块,WS 或轮询拿 `last_audio_path` 变更 → fetch wav → 播。解锁后全自动
- B. 首次加载弹"🔊 启用声音"按钮(点一次解锁,之后自动)——最简单的合规做法
- C. Gradio 组件级:`tts_player` 已 `autoplay=True`,补一个"页面首次点击时 broadcast 一个 dummy 播放"的 js_on_load hack

**验收**:sim 文本对话时,LLM 回复的语音**无需任何点击**自动从电脑音箱放出。

### P0-2:浏览器麦克风提取实测(sim 侧语音)

现状:`gr.Audio(sources=["microphone"], streaming=True)` 流式分句已实现(V2.3),但**真浏览器+真麦克风没实测过**(headless 无麦)。截图曾见"找不到麦克风"提示(headless 下正常)。

**调研/排错点**:
- localhost 是 secure context,`getUserMedia` 应可用;检查浏览器权限弹窗(首次点 ⏺ 时)
- Gradio 6 streaming chunk 的采样率/声道实测(预期 48kHz float;VAD 已兼容)
- 若 streaming 不稳,回退方案:V2.3 的手动录停组件已在(mic_input),语音模式可先用"点一次录一句"保底
- 参考 Gradio 官方指南:`automatic-voice-detection` / `real-time-speech-recognition`

### P0-3:SDK 语音识别链路调研(用户点名"核心调研 reachymini 的语音识别")

**现状已查明**:`reachy_mini` SDK **本身不含 ASR**,只提供音频采集(`media.get_audio_sample`)和播放。官方 `reachy_mini_conversation_app` 走两条路:HF Realtime API(需联网到 HF)或本地 `speech-to-speech` 后端(需 torch,RPi 跑不了)——本项目决策 13 已定:豆包 ASR WebSocket 流式(`asr/doubao_asr.py`,协议:bigmodel_nostream,全量推送,1s 无更新判稳定)。

**下轮可调研**:豆包 ASR 的实时流式(边录边识别,当前是录完一句送一句)+ 唤醒词("嗨 Reachy")减少环境误触发。

---

## 五、浏览器端完整功能清单(现状)

✅ 3D 交互视图(旋转/平移/缩放/实时跟随)| ✅ 运行模式下拉(纯仿真/真机有线/无线,失败回滚)| ✅ 对话模式切换(文本/语音免提)| ✅ 场景 MJPEG 对照(折叠)| ✅ 眼睛/真机相机副视角 | ✅ 工具轨迹/设置/手部跟随(模型已下)/声源跟随开关(默认关)| ✅ TTS 播放器(⚠️ autoplay 被浏览器拦,见 P0-1)

---

## 六、未 commit 改动(两 session 累积,建议尽快 commit)

本 session 新增/修改的关键文件:
- `reachymini_conversation/utils/camera_stream.py`(/ws/state + CORS + StaticFiles + real_frame_provider)
- `reachymini_conversation/app.py`(static_dir/ModeManager/RealVoiceLoop/logging/镜像注入)
- `reachymini_conversation/web_ui.py`(3D 主区/模式下拉/语音切换/声源开关)
- 新:`mode_manager.py` `real_voice.py` `local_audio.py` `local_camera.py` `voice_loop.py` `tools/export_visual_manifest.py` `tools/e2e_viewer_check.py` `SPEC_V2.md`
- `static/meshes/`(manifest+41 STL)、`static/js/`(viewer+vendor)
- 测试:`test_state_ws.py` `test_visual_manifest.py` `test_three_rig.mjs` `golden_rig_fixture.json` `test_mode_manager.py` `test_mode_switch_ui.py` `test_voice_loop.py` `test_real_voice.py` `test_v2_fixes.py`
- 修:`tools/dance.py` `brain/doubao_brain.py` `mirror_orchestrator.py` `sound_localizer.py` `tests/test_p5_sound_localizer.py`

建议 commit 拆分:`[V1] feat: three.js 交互式 3D 视图` / `[V2] feat: 运行模式切换+语音对话框架` / `[V2] fix: 真机实测四轮 bug`。

---

## 七、下轮新对话开场模板

```
我要继续推进 /home/seeed/Reachy_Mini_conversation。
请按顺序读:agents.local.md → HANDOVER_SESSION_6.md(最新)→ SPEC_V2.md → plan.md。

当前任务:HANDOVER_SESSION_6 §四 P0-1 —— 浏览器语音播报自动播放(autoplay 策略绕过)。
我的需求原话:"reachymini能够自动的讲话,而不是我需要再点击播放"。
做完 P0-1 后做 P0-2(浏览器麦克风 sim 侧实测)。

环境:三端口服务可用 ./scripts/start.sh --ui 起;真机是有线版(USB),用时下拉切
「真机+仿真(有线)」并先开电机电源。测试基线 pytest 192 全绿。
```
