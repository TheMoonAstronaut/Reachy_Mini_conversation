"""reachymini_conversation.app — Reachy Mini Conversation 主入口(P2)。

继承 SDK `ReachyMiniApp`,实现 `run()` 启动:
  - 后台线程:每秒读头部 RPY,写入 state bus
  - MirrorOrchestrator:sim + (可选) real 镜像(决策 9)
  - 独立 FastAPI app(端口 7861):MJPEG 推流 /sim_feed /camera_feed /healthz
  - 主 Gradio UI(端口 7860):视频 + 对话 + 设置(占位)

CLI:
  --ui                启动 Web UI(默认)
  --real              真机 + 仿真镜像(P2 启用)
  --preload-datasets  预下载 HF emotions(决策 16D,P7 启用)
  --head-poll-hz      头部 RPY 轮询频率(P1 调试用)
  --stream-port       MJPEG 推流端口(默认 7861)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

# SDK 基类
from reachy_mini.apps.app import ReachyMiniApp
from reachy_mini.reachy_mini import ReachyMini

# 包内部
from reachymini_conversation.hand_follower import HandFollower
from reachymini_conversation.mirror_orchestrator import MirrorOrchestrator
from reachymini_conversation.sound_localizer import SoundLocalizer
from reachymini_conversation.state_bus import get_state_bus
from reachymini_conversation.utils.camera_stream import create_camera_stream_app
from reachymini_conversation.web_ui import (  # noqa: E402
    REACHY_CSS,
    REACHY_THEME,
    _RM_LAN_JS as REACHY_LAN_JS,
    build_ui,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# config.py 在根目录(不是子包),用 sys.path 引入
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import config as _root_config  # noqa: E402


# ============================================================================
# 1. ReachyMiniApp 子类(P2 实现)
# ============================================================================
class ConversationApp(ReachyMiniApp):
    """Web UI + MJPEG 推流 + sim/real 镜像(P2)。"""

    custom_app_url: str | None = None
    dont_start_webserver: bool = True
    # P7.B:显式钉住 "default"(= 本地 IPC 优先,自动检测)。
    # SDK 默认其实也是 "default",这里显式声明让 CLI 入口开箱即用、
    # 不随 SDK 默认值变化而漂移。
    request_media_backend: str | None = "default"

    def __init__(
        self,
        head_poll_hz: float = 1.0,
        stream_port: int = 7861,
        stream_host: str = "0.0.0.0",
        target_fps: int = 15,
    ) -> None:
        super().__init__()
        self.head_poll_hz = head_poll_hz
        self.stream_port = stream_port
        self.stream_host = stream_host
        self.target_fps = target_fps

        self._poller_thread: threading.Thread | None = None
        self._sound_localizer: SoundLocalizer | None = None
        self._hand_follower: HandFollower | None = None
        self._demo: Any = None  # gradio.Blocks
        self._orchestrator: MirrorOrchestrator | None = None
        self._stream_runner: Any = None  # uvicorn.Server

    # ---------- ReachyMiniApp.run() ----------
    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        """主入口:镜像 + 头部 polling + MJPEG 推流 + Gradio,阻塞到 stop_event。"""
        bus = get_state_bus()
        bus.update_many({"status": "running", "error": None})

        # 1. MirrorOrchestrator(P2 决策 9)
        #    单一 sim 实例(P2 暂未启用 real;--real 时再加)
        run_mode = _root_config.RUN_MODE  # 来自根目录 config.RUN_MODE
        # 决策 3:默认 sim;--real 走 real_plus_sim
        self._orchestrator = MirrorOrchestrator(
            sim_mini=reachy_mini,
            real_mini=None,
            run_mode=run_mode,
        )
        bus.update("run_mode", run_mode)

        # 2. 后台 head poller(P1)
        logger.info(f"[P2] Starting head poller @ {self.head_poll_hz} Hz")
        self._poller_thread = threading.Thread(
            target=self._head_poller_loop,
            args=(reachy_mini, stop_event),
            daemon=True,
            name="head-poller",
        )
        self._poller_thread.start()

        # 2.5 声源定位(P5)
        logger.info("[P5] Starting sound localizer")
        self._sound_localizer = SoundLocalizer(
            orchestrator=self._orchestrator,
            doa_hz=10.0,
            return_to_center_sec=3.0,
        )
        self._sound_localizer.start()

        # V2 Fix D:真机眼睛相机(USB 直连,懒开)。提前创建 —— 手部跟随(P6)
        # 与 MJPEG /camera_feed(§3)共用同一实例,内部单抓帧线程写缓存,
        # 多消费者读缓存不抢帧(2026-09-17 P6 修复)。
        from reachymini_conversation.local_camera import UsbEyeCamera

        self._usb_eye = UsbEyeCamera()

        # 2.6 手部跟随(P6)— 默认关,UI/工具显式开
        logger.info("[P6] Initializing hand follower")
        try:
            sim_get_frame = (
                reachy_mini.media.get_frame_jpeg
                if hasattr(reachy_mini, "media") and reachy_mini.media is not None
                else None
            )

            def _hand_frame_getter() -> bytes | None:
                """取帧源:真机已连接 → 真机 USB 相机(真手在机器人镜头前);
                否则退回 sim 眼睛相机(合成画面,纯仿真下永远检测不到手,
                保持可用不崩)。真机相机未就绪返回 None,本轮跳过。"""
                if self._orchestrator is not None and self._orchestrator.real_mini is not None:
                    try:
                        frame = self._usb_eye.get_frame_jpeg()
                        if frame:
                            return frame
                    except Exception as e:
                        logger.debug(f"[P6] 真机相机取帧失败: {e}")
                if sim_get_frame is not None:
                    try:
                        return sim_get_frame()
                    except Exception:
                        return None
                return None

            self._hand_follower = HandFollower(
                orchestrator=self._orchestrator,
                get_frame_jpeg_fn=_hand_frame_getter,
                poll_hz=15.0,
            )
            self._hand_follower.start()
        except Exception as e:
            logger.warning(f"[P6] HandFollower 初始化失败: {e}")
            self._hand_follower = None

        # 2.7 注入 ToolDependencies 到 web_ui(P7 真 function calling 用)
        try:
            from reachymini_conversation.web_ui import set_sound_localizer, set_tool_deps
            from tools.core_tools import ToolDependencies

            # V2 Fix B:注入镜像适配器而不是裸 sim_mini —— 工具动作(dance/
            # play_emotion/move_head/look_at_sound)同步打到 sim + real 双实例。
            from reachymini_conversation.mirror_orchestrator import MirroredToolTarget

            deps = ToolDependencies(
                reachy_mini=MirroredToolTarget(self._orchestrator),
                movement_manager=None,
                hand_follower=self._hand_follower,
            )
            set_tool_deps(deps)
            set_sound_localizer(self._sound_localizer)
            logger.info("[P7] ToolDependencies(镜像目标)注入到 web_ui")
        except Exception as e:
            logger.warning(f"[P7] ToolDependencies 注入失败: {e}")

        # 2.8 V2:ModeManager(运行模式运行时切换:pure_sim ↔ real_plus_sim)
        self._real_voice_loop: Any = None
        try:
            from reachymini_conversation.mode_manager import ModeManager, set_mode_manager

            _mm = ModeManager(self._orchestrator)
            set_mode_manager(_mm)
            logger.info("[V2] ModeManager 就绪(UI 下拉可切换真机模式)")

            # 模式命令隔离(2026-09-20):--wired/--wireless 启动即自动连接,
            # 不必再点 UI。通路来自 start.sh 注入的 REACHYMINI_CONN。
            _conn = os.environ.get("REACHYMINI_CONN", "").strip().lower()
            if run_mode == "real_plus_sim" and _conn in ("wired", "wireless"):
                if _conn == "wireless":
                    _cfg: dict[str, Any] = {
                        "type": "wireless",
                        "host": os.environ.get("REACHYMINI_HOST", "reachy-mini.local"),
                        "port": int(os.environ.get("REACHYMINI_PORT", "8000")),
                    }
                else:
                    _cfg = {"type": "wired", "port": 8001}
                logger.info(f"[V2] 启动即自动连接真机({_conn})…")

                def _auto_connect() -> None:
                    try:
                        result = _mm.switch_to("real_plus_sim", _cfg)
                        if result.get("ok"):
                            logger.info(f"[V2] 自动连接成功({_conn})")
                        else:
                            logger.warning(
                                f"[V2] 自动连接失败:{result.get('error')} —— "
                                "可稍后点顶栏 ⚡ 重试"
                            )
                    except Exception as e:
                        logger.warning(f"[V2] 自动连接异常: {type(e).__name__}: {e}")

                threading.Thread(
                    target=_auto_connect, daemon=True, name="auto-connect"
                ).start()

            # V2.4:真机语音环路(real 模式 + 语音模式时采真机麦克风)
            from reachymini_conversation.web_ui import (
                get_pipeline,
                get_tool_deps_global,
                set_orchestrator,
            )
            from reachymini_conversation.real_voice import RealVoiceLoop

            set_orchestrator(self._orchestrator)
            pipeline = get_pipeline()
            # V2 fix(2026-09-16):real 语音路径漏注入 tool_deps —— LLM 无工具
            # 可调,只能把"调用 dance 工具"当文本念出来(伪调用,机器人不动)。
            # 与 web_ui 文本/录音路径共享同一 deps(镜像双实例)。
            _td = get_tool_deps_global()
            if _td is not None:
                pipeline.tool_deps = _td
            self._real_voice_loop = RealVoiceLoop(self._orchestrator, pipeline)
            self._real_voice_loop.start()
            logger.info("[V2] RealVoiceLoop 就绪(真机麦克风免提,待命)")
        except Exception as e:
            logger.warning(f"[V2] ModeManager/RealVoiceLoop 初始化失败: {e}")

        # 2.9 空闲待机呼吸(官方 BreathingMove 移植:无命令时头部微动+天线摆动)
        self._idle_breath: Any = None
        try:
            from reachymini_conversation.idle_breath import IdleBreathController
            from reachymini_conversation.mirror_orchestrator import MirroredToolTarget

            def _idle_pose_getter() -> Any:
                sim = self._orchestrator.sim_mini
                pose = sim.get_current_head_pose()
                _, antennas = sim.get_current_joint_positions()
                return pose, antennas

            self._idle_breath = IdleBreathController(
                MirroredToolTarget(self._orchestrator),  # 呼吸镜像到 sim + real
                pose_getter=_idle_pose_getter,
            )
            self._idle_breath.start()
        except Exception as e:
            logger.warning(f"[idle-breath] 初始化失败(不影响其余功能): {e}")

        # 3. MJPEG 推流 FastAPI(独立端口 7861)
        logger.info(f"[P2] Starting camera stream on {self.stream_host}:{self.stream_port}")
        try:
            import uvicorn

            # 场景流接收器(studio_close,UDP:5006 ← daemon_launcher 方案 B patch)。
            # 生命周期由 camera_stream 的 lifespan 托管(start 失败只记日志,
            # /scene_feed 自动走占位图,不影响其余链路)。
            from reachymini_conversation.utils.scene_stream import SceneUdpReceiver

            scene_receiver = SceneUdpReceiver()

            # V2 Fix D:真机眼睛相机(USB 直连,懒开;没插真机就走占位图)。
            # 复用 §2.5 的实例(与手部跟随共享抓帧缓存)。
            stream_app = create_camera_stream_app(
                sim_mini=reachy_mini,
                real_mini=None,
                target_fps=self.target_fps,
                scene_provider=scene_receiver,
                static_dir=str(PROJECT_ROOT / "static"),
                real_frame_provider=self._usb_eye,
            )
            stream_config = uvicorn.Config(
                stream_app,
                host=self.stream_host,
                port=self.stream_port,
                log_level="warning",
                lifespan="on",
            )
            self._stream_runner = uvicorn.Server(stream_config)
            stream_thread = threading.Thread(
                target=self._stream_runner.run,
                daemon=True,
                name="stream-server",
            )
            stream_thread.start()
        except Exception as e:
            logger.exception(f"[P2] camera stream 启动失败: {e}")
            bus.update("error", f"stream 启动失败: {e}")

        # 4. Gradio UI(端口 7860)
        logger.info("[P2] Launching Gradio UI @ http://localhost:7860")
        self._demo = build_ui()
        try:
            # B2:主题/CSS/JS 统一由 launch() 注入(Gradio 6.0 起 theme/css/js
            #     从 Blocks 构造器移到 launch(),放 Blocks 会触发 deprecation 警告)
            self._demo.launch(
                server_name="0.0.0.0",
                server_port=7860,
                theme=REACHY_THEME,
                css=REACHY_CSS,
                js=REACHY_LAN_JS,
                prevent_thread_lock=True,
                show_error=True,
                quiet=False,
            )
        except Exception as e:
            logger.exception(f"[P2] Gradio launch 失败: {e}")
            bus.update("error", f"Gradio 启动失败: {e}")
            bus.update("status", "stopped")
            raise

        logger.info("[P2] UI + Stream up. Waiting for stop_event.")
        stop_event.wait()

        # ---------- 清理 ----------
        bus.update("status", "stopping")
        try:
            if self._stream_runner is not None:
                self._stream_runner.should_exit = True
        except Exception:
            pass
        try:
            if self._demo is not None:
                self._demo.close()
        except Exception as e:
            logger.warning(f"[P2] Gradio close failed: {e}")
        if self._poller_thread is not None and self._poller_thread.is_alive():
            self._poller_thread.join(timeout=2.0)
        if self._sound_localizer is not None:
            self._sound_localizer.stop(timeout=2.0)
        if self._hand_follower is not None:
            self._hand_follower.stop(timeout=2.0)
        if self._real_voice_loop is not None:
            self._real_voice_loop.stop(timeout=2.0)
        bus.update("status", "stopped")

    # ---------- 后台:头部 RPY 轮询(P1)----------
    def _head_poller_loop(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        bus = get_state_bus()
        interval = 1.0 / max(self.head_poll_hz, 0.1)
        logger.info(f"[head-poller] started, interval={interval:.2f}s")

        while not stop_event.is_set():
            try:
                head_pose = reachy_mini.get_current_head_pose()
                head_joints, antennas = reachy_mini.get_current_joint_positions()

                import numpy as np

                pose_list = np.asarray(head_pose).tolist() if head_pose is not None else None
                joints_list = list(head_joints) if head_joints is not None else None
                antennas_list = list(antennas) if antennas is not None else None

                bus.update_many(
                    {
                        "head_pose": pose_list,
                        "head_joints": joints_list,
                        "antennas": antennas_list,
                        "error": None,
                    }
                )
            except Exception as e:
                err_msg = f"{type(e).__name__}: {e}"
                logger.warning(f"[head-poller] error: {err_msg}")
                bus.update("error", err_msg)

            if stop_event.wait(interval):
                break
        logger.info("[head-poller] stopped")


# ============================================================================
# 2. CLI 入口
# ============================================================================
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reachy-mini-conversation",
        description="Reachy Mini Conversation App(Web UI,左侧 Mujoco + 右侧对话)",
    )
    # --ui 保留为兼容开关(start.sh 传参),默认即 UI 模式(legacy CLI 已移除)
    parser.add_argument(
        "--ui",
        action="store_true",
        default=True,
        help="启动 Gradio Web UI(默认 0.0.0.0:7860,已默认开启)",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="[P2+ 启用] 真机 + 仿真镜像模式(需要 USB 真机)",
    )
    parser.add_argument(
        "--preload-datasets",
        action="store_true",
        help="[P7+ 启用] 预下载 HF emotions dataset(决策 16D)",
    )
    parser.add_argument(
        "--head-poll-hz",
        type=float,
        default=1.0,
        help="头部 RPY 轮询频率 Hz(默认 1.0)",
    )
    parser.add_argument(
        "--stream-port",
        type=int,
        default=7861,
        help="MJPEG 推流端口(默认 7861)",
    )
    return parser


def _lan_urls() -> list[str]:
    """本机局域网 IPv4 列表(用于启动横幅提示同 WiFi 访问链接)。

    UDP connect 不发包,只是借路由表选出对外网卡地址;无网卡/离线时返回空。
    """
    import socket

    ips: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))  # 无实际流量
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.append(ip)
        finally:
            s.close()
    except OSError:
        pass
    return ips


def main() -> int:
    args = _build_arg_parser().parse_args()

    # V2 修复:配置 logging 输出(之前完全没配,logger.info/warning 全丢,
    # 真机调试时 real-voice/mode_manager 的日志看不到 → 排错全靠猜)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # UI 模式(唯一入口;legacy CLI 已随开源清理移除)
    print("=" * 60)
    print("  Reachy Mini Conversation — Web UI 模式")
    print("  本机:      http://localhost:7860")
    for ip in _lan_urls():
        print(f"  局域网:    http://{ip}:7860   (同 WiFi 设备可访问)")
    print("  MJPEG:     http://localhost:7861/sim_feed")
    print("=" * 60)

    app = ConversationApp(
        head_poll_hz=args.head_poll_hz,
        stream_port=args.stream_port,
    )
    try:
        app.wrapped_run()
        return 0
    except KeyboardInterrupt:
        print("\n[reachy-mini-conversation] Ctrl+C, stopping...")
        app.stop()
        return 130
    except Exception:
        logger.exception("App 异常退出")
        return 1


if __name__ == "__main__":
    sys.exit(main())
