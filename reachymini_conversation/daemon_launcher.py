"""reachymini_conversation.daemon_launcher — 方案 B:app 侧 monkey-patch 的 daemon 启动器。

目标(决策:网页主区显示 MuJoCo 第三人称场景 + 副视角显示眼睛画面 + 无原生弹窗):
  1. 网页主区要 `studio_close` 相机(世界固定第三人称,640x640)的画面 ——
     SDK 1.10.0 定义了该相机但从未渲染过(零调用)。
  2. `--headless` 要关掉原生 MuJoCo viewer 弹窗,但 **保留视频流** ——
     SDK 1.10.0 里 headless=True 会连带不启动 eye_camera 的渲染线程
     (backend.py run() L211-217 的 `if not self.headless` 同时管弹窗和推流),
     需要把 "无弹窗" 与 "视频流" 解耦。

为什么不改 site-packages(方案 B 核心约束):
  SDK 是 pip 依赖,升级即丢补丁;monkey-patch 集中在 launcher 里,
  跟随本仓库版本控制,SDK 升级时一眼能看到要核对的地方。

patch 内容(reachy_mini SDK 1.10.0,backend/mujoco/backend.py):
  1. `MujocoBackend.rendering_loop` —— **替换**。
     原行为(L137-160):函数签名接收 `port`,但内部构造
     `GStreamerUDPCamera(width=..., height=..., log_level=...)` 时
     **不传 dest_port**,`port` 参数被静默忽略,永远发到 5005。
     替换实现与原版逐行一致,仅补传 `dest_port=port`。
  2. `MujocoBackend.run` —— **包装**。
     在原 run() 之前额外 spawn:
       - 始终:`rendering_loop(CAMERA_STUDIO_CLOSE, 5006)`(场景流,新增链路)
       - headless 时:`rendering_loop(CAMERA_REACHY, 5005)`(补上 SDK 跳过的眼睛流)
     model/data 在 MujocoBackend.__init__ 里已创建,run() 执行时必然就绪,
     无需等待重试;渲染线程与原 SDK 眼睛线程一样只读 self.data,无额外竞争。

patch 绑定位置说明:
  `daemon.py` 用 `from .backend.mujoco import MujocoBackend` 把类绑定进自己
  的命名空间 —— 但 patch 的是 **类对象本身的方法**(class attribute),
  无论谁 import 该类都拿到同一个类对象,所以 patch 类即对所有引用生效。
  唯一时序要求:patch 先于 `MujocoBackend.run()` 执行(即先于 SDK main())。

入口:
  python -m reachymini_conversation.daemon_launcher --sim --headless [--scene empty] ...
  参数与 SDK `reachy-mini-daemon` 完全一致(透传到 SDK 的 argparse)。
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from threading import Thread

logger = logging.getLogger(__name__)

# Renderer 构造串行化锁。
# 为什么需要:mujoco.Renderer 构造时会调 glfwInit(GLFW 后端,DISPLAY 在的桌面
# 环境默认走 GLFW/X11);glfwInit 不是线程安全的,两个渲染线程并发构造会触发
# pyGLFW 断言 `_glfw.x11.errorHandler == NULL`(x11_init.c)直接 abort/core dump。
# SDK 1.10.0 永远只有一路渲染线程(eye_camera)所以从不触发;我们加了 scene 路
# 之后必须串行化构造。构造完之后各线程用自己的 context 渲染,无需持锁
# (SDK 原本就是 viewer 主线程 + eye 渲染线程并发用 GL,没问题)。
_RENDERER_INIT_LOCK = threading.Lock()

# 与 SDK backend.py 的常量保持一致(CAMERA_REACHY/CAMERA_STUDIO_CLOSE);
# 这里复制字面量而不是 import,让 patch 逻辑自足、测试可纯 mock 注入。
CAMERA_REACHY = "eye_camera"
CAMERA_STUDIO_CLOSE = "studio_close"
CAMERA_SIZES = {CAMERA_REACHY: (1280, 720), CAMERA_STUDIO_CLOSE: (640, 640)}

# UDP 端口约定:5005 = 眼睛流(SDK 原链路,daemon media server 消费);
# 5006 = 场景流(本 launcher 新增,app 侧 scene_stream 消费)
EYE_UDP_PORT = 5005
SCENE_UDP_PORT = 5006

# 幂等标记:重复调用 patch_mujoco_backend() 不会叠包(测试会多次调用)
_PATCH_FLAG = "_reachy_conv_launcher_patched"


def _rendering_loop_with_port(self, camera_name: str, port: int) -> None:
    """替换 SDK 1.10.0 的 `MujocoBackend.rendering_loop`。

    与原版(backend.py L137-160)逐行一致,唯一差异:
    构造 GStreamerUDPCamera 时传 `dest_port=port` —— 原版忽略 `port` 参数,
    无论传什么都发到 5005(SDK bug,已在注释中注明待上游修复后可还原)。
    """
    # 延迟 import:gi/GStreamer 只在渲染线程真正启动时才需要,
    # 让 launcher 模块本身在无 gi 的环境(如 CI)也能 import。
    from reachy_mini.media.gstreamer_udp_camera import GStreamerUDPCamera

    camera_size = CAMERA_SIZES[camera_name]
    frame_sender = GStreamerUDPCamera(
        dest_port=port,  # ← FIX:SDK 1.10.0 原行为是不传 → 永远 5005
        width=camera_size[0],
        height=camera_size[1],
        log_level=logging.getLevelName(self.logger.level),
    )
    frame_sender.start()
    # 见 _RENDERER_INIT_LOCK 注释:glfwInit 非线程安全,Renderer 构造必须串行
    with _RENDERER_INIT_LOCK:
        offscreen_renderer = self._get_renderer(camera_name)
        camera_id = self._get_camera_id(camera_name)

    while not self.should_stop.is_set():
        start_t = time.time()
        offscreen_renderer.update_scene(self.data, camera_id)

        im = offscreen_renderer.render()
        frame_sender.send_frame(im)

        took = time.time() - start_t
        time.sleep(max(0, self.rendering_timestep - took))


def _spawn_rendering_thread(self, camera_name: str, port: int, name: str) -> Thread:
    """以 daemon 线程启动一路渲染,异常只记日志不影响 daemon 主流程。"""

    def _guarded_rendering_loop() -> None:
        try:
            self.rendering_loop(camera_name, port)
        except Exception:
            logger.exception(
                f"[daemon-launcher] {name} 渲染线程异常退出"
                f"(camera={camera_name}, port={port});其余链路不受影响"
            )

    thread = Thread(
        target=_guarded_rendering_loop,
        daemon=True,
        name=f"render-{name}",
    )
    thread.start()
    logger.info(f"[daemon-launcher] 渲染线程已启动:{name} → UDP:{port}")
    return thread


def patch_mujoco_backend(mj_backend_module=None) -> None:
    """对 SDK MujocoBackend 做最小侵入 monkey-patch(幂等)。

    Args:
        mj_backend_module: `reachy_mini.daemon.backend.mujoco.backend` 模块。
            默认 None → 运行时延迟 import(生产路径);测试可注入假模块,
            避免 import mujoco/gi。

    patch 点(详见模块 docstring):
        1. rendering_loop:替换,补传 dest_port=port(修 SDK 1.10.0 port 忽略 bug)
        2. run:包装,前置 spawn 场景渲染线程;headless 时补眼睛渲染线程
    """
    if mj_backend_module is None:
        from reachy_mini.daemon.backend.mujoco import backend as mj_backend_module

    cls = mj_backend_module.MujocoBackend
    if getattr(cls, _PATCH_FLAG, False):
        logger.debug("[daemon-launcher] MujocoBackend 已 patch,跳过重复 patch")
        return

    orig_run = cls.run

    def _run_with_scene_stream(self, *args, **kwargs):
        """包装 SDK run():先起渲染线程,再走原流程(含弹窗/主循环)。

        - 场景流(studio_close→5006):无条件启动,这是网页主区画面。
        - 眼睛流(eye_camera→5005):仅 headless 时由这里补;非 headless 时
          原 run() 自己会起(SDK L214-217),不能重复起(双发同端口会抢)。
        """
        _spawn_rendering_thread(self, CAMERA_STUDIO_CLOSE, SCENE_UDP_PORT, "scene")
        if self.headless:
            # headless 解耦:SDK 的 `if not self.headless` 把弹窗和眼睛推流
            # 绑在一起,headless=True 时两者都没。这里补回眼睛流,
            # 让 --headless 只表示"无原生弹窗",不表示"无视频"。
            _spawn_rendering_thread(self, CAMERA_REACHY, EYE_UDP_PORT, "eye")
        return orig_run(self, *args, **kwargs)

    cls.rendering_loop = _rendering_loop_with_port
    cls.run = _run_with_scene_stream
    setattr(cls, _PATCH_FLAG, True)
    logger.info(
        "[daemon-launcher] MujocoBackend patched:"
        " rendering_loop(dest_port 修复) + run(studio_close:5006 场景流"
        " + headless 时补 eye:5005)"
    )


_PATCH_AUDIO_FLAG = "_reachy_conv_audio_sink_patched"


def patch_sim_audio_sink(media_modules=None) -> None:
    """sim daemon 的播放 sink 固定走 PC 默认输出(不动 SDK 文件,幂等)。

    背景(2026-09-16 用户实测 bug):USB 连着真机时,sim 模式的 TTS 回答
    从真机喇叭出来(期望电脑音箱)。根因:SDK 按名字 "Reachy Mini Audio"
    匹配声卡做 Sink(device_detection.DEFAULT_AUDIO_TARGET)——该假设是
    "有卡=机器人场景",与本项目 sim 语义(PC 仿真、声音从电脑出)冲突。

    修复:monkey-patch 所有 from-import 了 get_audio_device 的模块
    (audio_base / audio_gstreamer / media_server;device_detection 本体
    也一并替换,防其他调用方),Sink 查询返回 None → SDK 回落
    autoaudiosink(PulseAudio 默认输出=电脑音箱),同时官方扬声器 EQ
    (为 Reachy 喇叭校准)也被跳过;Source 保持原名匹配(sim 下真机麦
    采集无人消费,无害;real 模式媒体在 daemon B --no-media,与本进程无关)。
    只影响本 daemon 进程。
    """
    if media_modules is None:
        import importlib

        media_modules = []
        for name in (
            "reachy_mini.media.device_detection",
            "reachy_mini.media.audio_base",
            "reachy_mini.media.audio_gstreamer",
            "reachy_mini.media.media_server",
        ):
            try:
                media_modules.append(importlib.import_module(name))
            except Exception:
                pass  # 个别模块在缺插件的平台 import 失败,跳过

    patched_any = False
    for mod in media_modules:
        orig = getattr(mod, "get_audio_device", None)
        if orig is None or getattr(orig, _PATCH_AUDIO_FLAG, False):
            continue

        def _make_wrapper(orig_fn):
            def _get_audio_device(device_type: str = "Source"):
                if device_type == "Sink":
                    logger.info(
                        "[daemon-launcher] sim 模式:播放 sink 走 PC 默认输出"
                        "(跳过 Reachy Mini Audio 卡匹配)"
                    )
                    return None
                return orig_fn(device_type)

            setattr(_get_audio_device, _PATCH_AUDIO_FLAG, True)
            return _get_audio_device

        mod.get_audio_device = _make_wrapper(orig)
        patched_any = True
    if patched_any:
        logger.info(
            "[daemon-launcher] sim 音频 sink 已 patch:Sink → PC 默认输出"
            "(EQ 随之跳过)"
        )


def main() -> None:
    """launcher 入口:先 patch,再原样调 SDK daemon main()(argparse 全透传)。"""
    patch_mujoco_backend()
    patch_sim_audio_sink()

    # patch 完成后再 import SDK daemon 入口并执行;sys.argv 原样透传,
    # --sim/--headless/--no-media/--scene 等全部由 SDK 自己的 argparse 解析。
    from reachy_mini.daemon.app.main import main as sdk_daemon_main

    sdk_daemon_main()


if __name__ == "__main__":
    sys.exit(main())
