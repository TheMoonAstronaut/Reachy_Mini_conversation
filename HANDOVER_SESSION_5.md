# SESSION 5 SUMMARY — 交接文档(新对话开场用)

> 给**下一段新对话的 AI 助手**的快速上下文。
> 配合 `agents.local.md` + `plan.md` + `HANDOVER_SESSION_4.md` 使用。

---

## TL;DR(一句话)

本 session **验证了启动链路全部正常 + 修了 MJPEG Content-Type 缺失的 P7.D bug(浏览器端视频黑屏根因)+ 收到关键反馈"用户要的是 MuJoCo 弹窗那种交互式 3D 视图,不是 MJPEG 图片"**;142 测试全绿。**下轮重点:决定 MuJoCo 弹窗嵌入浏览器的实现路径(方案 A/B/C/D 已对比,推荐方案 B three.js 重渲染)。**

---

## 一、本 session 完成的事

### 1. 启动链路实测验证(原 C5 任务)
| 验证项 | 结果 | 证据 |
|---|---|---|
| `./scripts/start.sh --ui` | ✅ | daemon + UI + MJPEG 三端口起(7860/7861/8000) |
| daemon headless | ✅ | 无原生 MuJoCo 弹窗(xdotool 验证) |
| `/scene_feed` | ✅ | 640x640 真帧,90KB,43000+ 帧连续 |
| `/sim_feed` | ✅ | 1280x720 真帧,116KB,10000+ 帧 |
| `/healthz` | ✅ | `{sim:true, scene:true, real:false}` |
| LLM 配置 | ✅ | `~/.reachymini/env.json` 已配(见 §三.5 模型 ID 修正) |
| TTS edge-tts | ✅ | subprocess 调用 OK(偶发网络瞬时失败会返回 None) |

### 2. 修复 P7.D:MJPEG Content-Type 缺失(浏览器黑屏根因)

**根因**:`camera_stream.py` 的 `add_api_route(..., response_class=StreamingResponse)` 让 FastAPI 自动包装 async generator,但**不传 media_type**,响应头缺:
```
Content-Type: multipart/x-mixed-replace; boundary=--frame
```
浏览器无法识别为 MJPEG → `<img>` 黑屏/不渲染。

**修复**:新增 `MJPEGStreamingResponse` 子类(在 `utils/camera_stream.py`):
```python
class MJPEGStreamingResponse(StreamingResponse):
    def __init__(self, content, **kwargs):
        kwargs.setdefault("media_type", "multipart/x-mixed-replace; boundary=--frame")
        super().__init__(content, **kwargs)
```
路由 `response_class=MJPEGStreamingResponse`(3 处:/sim_feed, /scene_feed, /camera_feed)。

**为什么保留 endpoint 是 async generator**:P7.A 回归测试断言 `inspect.isasyncgenfunction(endpoint)`(防"Response 实例当 endpoint"的 Bug A),改成"endpoint 返回 StreamingResponse 实例"会让 9 个测试失败。子类方案两全。

**验证**:24 个 camera_stream/scene_feed 测试全绿。

### 3. 修正 `~/.reachymini/env.json` 的模型 ID

**问题**:用户配置的 `doubao-seed-2-1-pro-260628` 不走标准 OpenAI function calling,输出伪 `<|FunctionCallBegin|>` 标记,工具调用失效。

**改成**:`doubao-seed-character-251128`(.env.example / plan.md §4.5 推荐)。

**验证**:
- 输入"跳个舞" → 真 function calling → `dance(simple_nod)` 工具被正确调用
- 输入"你好" → 自动调 `play_emotion(hello)` + `move_head(front)`

### 4. 排除一个假 bug

`web_ui.py:97` `MagicMock(spec=ReachyMini)` 看似 import 顺序错(MagicMock 在 L103 才 import)。**实测不触发**:Python 函数体内的名字是运行时绑定,模块加载完后 MagicMock 已在命名空间。**不是 bug**。

---

## 二、当前系统状态(实测)

```
启动:./scripts/start.sh --ui  ✅ 一键可用
浏览器:http://localhost:7860  ✅ HTTP 200
场景流:640x640 @ studio_close ✅
眼睛流:1280x720 @ eye_camera  ✅
LLM:function calling ✅(8 个工具)
TTS:edge-tts ✅(zh-CN-XiaoxiaoNeural)
ASR:豆包 WebSocket 流式 ✅(配置 OK,未实测端到端)
```

---

## 三、⚠️ 关键警示(沿袭 SESSION_4 + 新增)

1. **SDK site-packages 补丁**:`reachy_mini/media/media_server.py`(P7.B FIX)+ `gstreamer_udp_camera.py`(appsrc caps)。`pip install -U reachy_mini` 会丢。
2. **自编译插件**:`~/.local/share/gstreamer-1.0/plugins/libgstunixfd.so`,重建方法 `tools/gst_unixfd_backport/build.sh`。
3. **代理变量**:任何手动 python/curl 前必须 `unset HTTPS_PROXY HTTP_PROXY ALL_PROXY https_proxy http_proxy all_proxy`。
4. **daemon 日志噪音(无害)**:Central Relay 8443 重试 + TURN DNS 解析失败,是 media_server patch 禁用 webrtcsink 的预期副作用。
5. **(新)模型 ID 敏感**:LLM 必须用 `doubao-seed-character-251128`,不要用 `doubao-seed-2-1-pro-*` 系列(伪 function calling)。
6. **(新)Gradio 6 gr.HTML 的 MJPEG 限制**:`<img>` 标签能渲染,但 MJPEG 流必须带正确的 `Content-Type: multipart/x-mixed-replace`(P7.D fix)。

---

## 四、🔴 关键用户反馈:浏览器端应该是"真·MuJoCo 交互",不是视频流

**用户原话**(两段印证):
1. "之前想要的在浏览器里面封装 mojuco 弹窗,不知道怎么只变成了图片"
2. "我真正需要的是真实的 mujoco 的仿真系统潜入到网页里面,**保留鼠标左右按键的视角转换等等功能**"

**当前实现**:`<img src="/scene_feed">` 接的是 MJPEG(Motion JPEG,multipart/x-mixed-replace 连续 JPEG 帧)。本质是**视频流**,只能看,不能交互。

**用户期望**:类似 MuJoCo 原生 viewer 的**交互式 3D 仿真**嵌入浏览器:
- 鼠标左键拖拽 = 旋转视角
- 鼠标右键拖拽 = 平移视角
- 滚轮 = 缩放
- (理想)Ctrl + 右键 = 施加力/扰动
- 实时跟随物理仿真状态

### 方案对比(下轮必须做选择)

| 方案 | 思路 | 鼠标交互 | 物理仿真 | 工作量 | 推荐度 |
|---|---|---|---|---|---|
| **A. MuJoCo WASM** | 浏览器跑 mujoco npm 包(官方 WASM 编译版),加载 Reachy MJCF XML,订阅 daemon 状态驱动 | ✅ 完整原生 viewer 交互 | ✅ 真物理 | **大**(2-3 天) | ⭐⭐ 如果用户坚持要"真 MuJoCo" |
| **B. three.js 重渲染** | 浏览器加载 Reachy STL mesh,订阅 daemon 关节角度,three.js 渲染 | ✅ 旋转/缩放/平移 | ❌ 只是视觉镜像 | **中**(1-2 天) | ⭐⭐⭐⭐ 性价比最高 |
| **C. noVNC 转发** | daemon 端 Xvfb 跑原生 viewer,浏览器 noVNC 嵌入 | ✅ 原生 viewer 所有交互 | ✅ 真物理 | 中(需 X11) | ⭐ 太重,延迟高 |

### 推荐路径:先 B 保底,再 A 升级

**Phase 1(下轮优先):方案 B — three.js 重渲染**
- **已确认 SDK 资源齐备**:
  - STL mesh:`reachy_mini/descriptions/reachy_mini/mjcf/assets/*.stl`(visual + collision 两套,几十个零件)
  - MJCF XML:`mjcf/reachy_mini.xml` + `scene.xml` + `scenes/empty.xml`
  - 状态订阅 API:`ReachyMini.get_current_joint_positions()` 返回 `(head_joints[7], antennas[2])`;`get_current_head_pose()` 返回 4x4 变换矩阵
- **技术栈**:three.js + STLLoader + OrbitControls
- **通信**:WebSocket(StateBus 当前是 Gradio Timer 轮询,要加 WebSocket 通道)
- **验收**:浏览器主区可鼠标交互 3D Reachy,实时跟随 daemon 状态

**Phase 2(可选升级):方案 A — MuJoCo WASM**
- 适用场景:用户需要"在浏览器里施力看物理响应"等真物理交互
- 技术栈:`mujoco` npm 包(官方 WASM)
- 工作量大,需要深调研 MJCF 打包、状态同步频率、双向控制

### 不推荐:方案 C noVNC
- 需要 daemon 机器跑 X server(Xvfb)
- 延迟高(图像压缩 + 远程桌面协议)
- 不适合多用户(每个浏览器连同一个 X session 会冲突)
- 违背"无线版 RPi 跑 daemon"的架构(决策 6/13)

---

## 五、浏览器端完整功能清单(现状 vs 缺失)

### ✅ 已有
- 顶部状态栏(4 个 pill 徽章:系统状态/模式/声源/手部跟随)
- 主区:场景 MJPEG(studio_close 第三人称)
- 副视角:眼睛 MJPEG(sim)/ 真机 MJPEG(real)
- 对话面板:Chatbot + 文字输入 + 🎤 麦克风 + 🔊 TTS 播放器
- 手部跟随 Accordion(启停按钮)
- 设置 Accordion(API Key 编辑 + 保存到 env.json)
- 工具调用轨迹 Accordion(JSON)
- 开发者调试 Accordion(head RPY)
- Footer(项目路径/文档链接)

### ❌ 缺失(按用户痛感和优先级)
1. **🔴 真交互式 3D 视图**(本 session 用户核心反馈,方案 B 待实现)
2. **🟡 机器人手动控制面板**(虚拟摇杆 / 预设姿势按钮,绕过 LLM 直接控)
3. **🟡 情感/表情可视化**(当前 emotion 状态指示器,play_emotion 调用后反馈)
4. **🟡 声源定位罗盘**(DoA 角度可视化,圆形刻度盘)
5. **🟢 对话历史导出**(导出为 JSON/Markdown)
6. **🟢 多模态输入**(发图片给 LLM 分析,需豆包 vision 模型)
7. **🟢 场景相机切换**(studio_close / eye_camera / 其他 XML 相机)
8. **🟢 系统资源监控**(CPU / 内存 / 帧率)

---

## 六、整体开发规划(下轮开始)

### 优先级 P0(核心需求,必须先做)
**任务 V1:真·MuJoCo 交互式 3D 视图嵌入浏览器**

**用户明确要求**(原话):"保留鼠标左右按键的视角转换等等功能"。

**方案选择**:推荐 **方案 B(three.js 重渲染)**,保底实现鼠标交互。如果用户坚持要"真物理",升级 **方案 A(MuJoCo WASM)**。

**拆分**:
- V1.1 **状态订阅通道**:StateBus 加 WebSocket(或 SSE)推送 `head_joints + antennas + head_pose` @ 30 Hz(当前是 Gradio Timer 1 Hz 轮询,频率太低且不能驱动 3D)
- V1.2 **STL 静态资源**:复制 `reachy_mini/descriptions/reachy_mini/mjcf/assets/*.stl`(visual 目录,跳过 collision)到 `static/meshes/`;Gradio 用 `app.mount_static_files` 或 FastAPI `StaticFiles` 暴露
- V1.3 **three.js 前端**(`reachymini_conversation/web_ui/three_viewer.js`):
  - 加载所有 STL,按 MJCF XML 的 body 层级组装
  - OrbitControls 提供鼠标交互(左键旋转 / 右键平移 / 滚轮缩放)
  - WebSocket 订阅状态 → 更新关节角度
- V1.4 **Gradio 集成**:`gr.HTML` + `js_on_load` 注入 three.js 场景(参考 P7.D 学到的:Gradio 6 的 gr.HTML 用 `innerHTML` 渲染,JS 可执行)
- V1.5 **UI 布局调整**:主区从 MJPEG 切换到 three.js 3D 视图;眼睛 MJPEG 保留作副视角
- **验收**:
  - 浏览器主区可鼠标旋转/缩放/平移 3D Reachy
  - Reachy 头部实时跟随 daemon 状态(跳舞/摆头时 3D 模型同步)
  - 保留原 MJPEG 副视角(眼睛相机)

**备选**:如果 three.js 方案用户不接受(坚持要"真物理"),切方案 A(MuJoCo WASM):
- 调研 `mujoco` npm 包(WASM 版)
- 打包 Reachy MJCF + mesh 为 WASM 可加载格式
- 浏览器端跑 MuJoCo 引擎,daemon 状态作为"外力"同步
- 工作量 2-3 天,风险高(状态一致性)

### 优先级 P1(功能补全)
**任务 F1:real 模式实装**
- 修 `app.py:93` `real_mini=None` 硬编码
- `--real` 时创建第二个 `ReachyMini(use_sim=False)` 实例
- MirrorOrchestrator 双实例同步

**任务 F2:TTS 播报路径统一**
- web_ui 文字对话也调 `pipeline._speak`(推机器人)
- 或者统一走 `_tts_and_play`,但加 `push_audio_fn`

**任务 F3:legacy 清理(决策 7 落地)**
- 删 `asr.py/audio.py/brain.py/gradio_personality.py/robot.py/tts.py/utils.py/main.py/__init__.py`
- 或者保留 `main.py` 作 fallback,其他删
- 修 `pyproject.toml` 移除 `audio_animation` include

### 优先级 P2(体验提升)
**任务 F4:HandLandmarker auto-download**
- 首次启动从 `storage.googleapis.com/mediapipe-models/.../hand_landmarker.task` 下载(230MB)
- 参考决策 16D 的 play_emotion HF 下载模式

**任务 F5:浏览器端"快速操作"面板**
- 预设姿势按钮(立正/蹲下/挥手)
- 虚拟摇杆(头 yaw/pitch/roll)
- 绕过 LLM 直接调工具

**任务 F6:C6 文档更新**
- ARCHITECTURE.md 视频链路章节(场景流 5006)
- TROUBLESHOOTING.md 场景流排障 + P7.D MJPEG Content-Type
- 本交接文档归档

### 优先级 P3(清理)
- temp_tts.wav 删除
- pyproject.toml audio_animation include 移除
- StateBus 预定义 `doa_angle_rad` 等动态 key

### 优先级 P4(扩展,plan.md §7 明确不在本期)
- 视频流 WebRTC 化(需 webrtcsink)
- 语音唤醒词("嗨,Reachy")
- HF Space 打包
- Windows 完整适配
- GitHub 开源发布

---

## 七、下轮新对话开场模板

```
我要继续推进 /home/seeed/Reachy_Mini_conversation。
请按顺序读:agents.local.md → HANDOVER_SESSION_5.md(最新)→ plan.md。

当前任务:按 HANDOVER_SESSION_5 §六 P0 任务 V1 执行 —— 真·MuJoCo 交互式 3D 视图嵌入浏览器。

我的需求(原话):"保留鼠标左右按键的视角转换等等功能"。
即:鼠标左键拖拽旋转视角、右键拖拽平移、滚轮缩放,类似 MuJoCo 原生 viewer 的交互。

我已确认走方案 B(three.js 重渲染),不做方案 A(MuJoCo WASM,工作量太大)。
如果 three.js 方案的"视觉镜像"不满足我,再升级方案 A。

动手前先确认启动状态:./scripts/start.sh --ui 应该能起,浏览器 7860 应该能看到
MJPEG 视频流(P7.D 已修,Content-Type 正确)。
```

---

## 八、本 session 遗留待 commit 的改动

- `reachymini_conversation/utils/camera_stream.py`:新增 `MJPEGStreamingResponse` 类 + 3 处路由改用
- `~/.reachymini/env.json`:model 改为 `doubao-seed-character-251128`(不入 git)

测试基线:**142 passed / 0 failed**(修复 MJPEG 后仍全绿)
