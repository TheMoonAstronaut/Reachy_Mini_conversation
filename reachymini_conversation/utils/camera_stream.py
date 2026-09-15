"""utils.camera_stream — FastAPI MJPEG 推流(P2 + 场景流)。

从 `ReachyMini.media.get_frame_jpeg()` 拉 JPEG,转 MJPEG 流。
前端 Gradio 用 `<img src="http://localhost:7861/sim_feed">` 嵌入。

场景流(studio_close 第三人称,640x640):
  daemon_launcher 的方案 B patch 让 daemon 把 studio_close 相机渲染发到
  UDP:5006;app 侧 `utils.scene_stream.SceneUdpReceiver` 收帧,经本模块的
  `/scene_feed` + `/scene_feed_status` 暴露,与 /sim_feed 共用同一套
  探针 + available 新鲜度语义 + 占位图机制。

为什么用 MJPEG 而不是 WebRTC / HLS:
  - 浏览器 <img> 直接吃 MJPEG(`multipart/x-mixed-replace`),无需 JS 库
  - Gradio HTML 组件一行 HTML 嵌入
  - 延迟低(每帧 ~30-100ms)
  - 不需要 opus / h264 codec 协商
  - 后续 P5+ 升级 WebRTC 可选

注意:这是个独立的 FastAPI app(端口 7861),不和 Gradio(7860)混,
避免 Gradio 6.0 内部路由冲突。

Bug fix (P7.A):endpoint 必须用 `add_api_route` + `response_class=StreamingResponse` 注册,
不能用 `app.get(path)(Response 实例)` —— FastAPI 不支持 Response 实例当 endpoint。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)


def create_camera_stream_app(
    sim_mini: object | None,
    real_mini: object | None = None,
    *,
    target_fps: int = 15,
    probe_interval: float = 0.5,
    stale_after: float = 2.0,
    scene_provider: object | None = None,
    state_broadcast_hz: float = 25.0,
    static_dir: str | None = None,
    real_frame_provider: object | None = None,
) -> FastAPI:
    """构造一个 FastAPI app,提供 MJPEG 推流 endpoint。

    Args:
        sim_mini: ReachyMini 实例(use_sim=True)。提供 sim 视频流(眼睛相机)。
        real_mini: ReachyMini 实例(use_sim=False)。可选,提供真机视频流。
        target_fps: 推流目标帧率(默认 15,降低 CPU 占用)。
        probe_interval: 后台探针拉帧间隔秒数(默认 0.5)。
            Bug fix (P7.C):stats 原来只在 MJPEG 流生成器里更新 —— 即必须有
            浏览器 <img> 连着 /sim_feed 才会统计。冷启动时相机初始化慢(前几秒
            get_frame_jpeg 返回 None),UI tick(1s)看到 available=false 就把
            <img> 换成占位 HTML → 浏览器断开 MJPEG → 之后相机 ready 了也没有任何
            客户端 → frames_received 永远 0 → UI 永久卡在占位图(粘性 fallback
            死锁)。探针与客户端无关地持续探测,让 available 永远反映相机真实状态。
        stale_after: 最近一次真帧超过该秒数则判定 available=False(默认 2.0)。
            Bug fix (P7.C):available 原来是 `frames_received > 0` 单调粘 true
            (曾经收到过帧就永远 true),流断了 UI 仍显示 <img>(裂图/黑屏)。
            改成新鲜度语义后 available 可 true↔false 双向切换,UI tick 能自动
            在 真视频 ↔ 占位图 之间来回切。
        scene_provider: 场景流(studio_close)帧源,需暴露 `get_frame_jpeg()`
            (如 `utils.scene_stream.SceneUdpReceiver`)。可选;有 start()/stop()
            方法时由 lifespan 托管(启动失败只记日志,走占位图,不 crash)。

    Endpoints:
        GET /sim_feed          - sim 视频流(MJPEG),眼睛相机 real frames
        GET /scene_feed        - 场景视频流(MJPEG,studio_close 640x640)
        GET /camera_feed       - 真机视频流(MJPEG),无真机返回 503
        GET /healthz           - 健康检查
        GET /sim_feed_status   - sim feed 状态(JSON),前端轮询判断显示真视频还是占位图
        GET /scene_feed_status - 场景 feed 状态(JSON,语义同 sim_feed_status)

    P7.A fallback:如果 sim_mini.media.get_frame_jpeg() 连续返回 None (例如沙箱缺
    GStreamer webrtc plugin / mujoco GL context),/sim_feed 自动 fallback 到一张带说明文字的
    占位 JPEG。占位图只是"等待中"的临时输出:流生成器每轮仍先尝试真帧,真帧一恢复
    (consecutive_misses 清零)就立刻切回真视频,占位模式不粘。
    前端轮询 /sim_feed_status 拿 available 字段决定是否显示"沙箱限制"提示。
    """
    # P7.C stats: 由后台探针 + MJPEG 流共同更新。
    #   frames_received/frames_attempted: 累计计数(单调,做健康指标)
    #   last_frame_at: 最近一次拿到真帧的 monotonic 时间戳(算 available 用)
    sim_feed_stats: dict[str, object] = {
        "frames_received": 0,
        "frames_attempted": 0,
        "available": False,
        "last_frame_at": None,
    }
    # 场景流(studio_close)stats,语义与 sim_feed_stats 完全一致
    scene_feed_stats: dict[str, object] = {
        "frames_received": 0,
        "frames_attempted": 0,
        "available": False,
        "last_frame_at": None,
    }

    def _is_fresh(stats: dict[str, object]) -> bool:
        """available = 最近 stale_after 秒内拿到过真帧(双向,可恢复可过期)。"""
        last = stats["last_frame_at"]
        if last is None:
            return False
        return (time.monotonic() - last) < stale_after

    def _is_available() -> bool:
        return _is_fresh(sim_feed_stats)

    # V1.1 WebSocket 客户端集合(/ws/state)。模块级函数外置,方便测试替换。
    ws_state_clients: set[WebSocket] = set()

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        """uvicorn lifespan="on" 时启动后台探针;shutdown 时取消。

        注意:Starlette TestClient 只有 `with TestClient(app)` 才跑 lifespan,
        老测试(无 with)行为不变。
        """
        probe_task: asyncio.Task | None = None
        if sim_mini is not None:
            probe_task = asyncio.create_task(
                _frame_probe_loop(sim_mini, sim_feed_stats, probe_interval),
                name="sim-frame-probe",
            )
        scene_probe_task: asyncio.Task | None = None
        if scene_provider is not None:
            # 管线启动失败(缺 gst 元素等)只记日志 → 探针永远拿 None → 占位图
            start_fn = getattr(scene_provider, "start", None)
            if callable(start_fn):
                try:
                    await asyncio.to_thread(start_fn)
                except Exception as e:
                    logger.warning(f"[camera-stream/scene] scene_provider.start() 失败: {e}")
            scene_probe_task = asyncio.create_task(
                _frame_probe_loop(_FrameProviderShim(scene_provider), scene_feed_stats, probe_interval),
                name="scene-frame-probe",
            )
        # V2 Fix D:real_frame_provider(UsbEyeCamera)的 start/stop 也托管
        # (懒开 v4l2 管线;相机没插时 start 内部静默,走占位图)
        if real_frame_provider is not None:
            start_fn = getattr(real_frame_provider, "start", None)
            if callable(start_fn):
                try:
                    await asyncio.to_thread(start_fn)
                except Exception as e:
                    logger.warning(f"[camera-stream/real] provider.start() 失败: {e}")
        # V1.1:25Hz 状态广播(three.js 3D 视图数据通道)
        state_task = asyncio.create_task(
            _state_broadcast_loop(sim_mini, ws_state_clients, hz=state_broadcast_hz),
            name="state-broadcast",
        )
        try:
            yield
        finally:
            for t in (probe_task, scene_probe_task, state_task):
                if t is not None:
                    t.cancel()
                    with suppress(asyncio.CancelledError):
                        await t
            stop_fn = getattr(scene_provider, "stop", None) if scene_provider is not None else None
            if callable(stop_fn):
                try:
                    await asyncio.to_thread(stop_fn)
                except Exception as e:
                    logger.debug(f"[camera-stream/scene] scene_provider.stop() 异常: {e}")
            if real_frame_provider is not None:
                stop_fn = getattr(real_frame_provider, "stop", None)
                if callable(stop_fn):
                    try:
                        await asyncio.to_thread(stop_fn)
                    except Exception as e:
                        logger.debug(f"[camera-stream/real] provider.stop() 异常: {e}")

    app = FastAPI(title="Reachy Mini Camera Stream", lifespan=_lifespan)
    frame_interval = 1.0 / max(target_fps, 1)

    # V1.2:静态资源伺服(STL mesh + three.js vendor + viewer.js)。
    # 挂在 7861 与 /ws/state 同 origin,Gradio(7860)页面跨源 fetch 由下方
    # CORS 放行。目录不存在时跳过(如测试环境没跑 export_visual_manifest)。
    if static_dir is not None:
        from pathlib import Path as _Path

        from fastapi.staticfiles import StaticFiles

        if _Path(static_dir).is_dir():
            app.mount("/static", StaticFiles(directory=static_dir), name="static")
        else:
            logger.warning(f"[camera-stream] static_dir 不存在,跳过挂载: {static_dir}")

    # V1.1/V1.2:Gradio(7860)页面里的 three.js 要从本 app(7861)跨源拉
    # STL/JS/WS 初始帧 —— 放开 7860 来源的 GET 跨域(WS 不受 CORS 约束,
    # 但 script/fetch 受)。仅本地回环,无安全风险。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:7860",
            "http://127.0.0.1:7860",
        ],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    # ---------- /sim_feed ----------
    # Bug fix (P7.A):endpoint 必须是 callable(async generator),不能是 Response 实例。
    # Bug fix (P7.D):response_class 用 MJPEGStreamingResponse(自动带正确 Content-Type)。
    if sim_mini is not None:
        sim_endpoint = make_mjpeg_endpoint(
            sim_mini, frame_interval, label="sim",
            stats=sim_feed_stats,
            fallback_jpeg_factory=lambda: generate_placeholder_jpeg(
                "SIM VIDEO UNAVAILABLE\nsandbox missing webrtc/GL plugin\n真环境会显示 Mujoco 仿真"
            ),
        )
        app.add_api_route(
            "/sim_feed",
            sim_endpoint,
            methods=["GET"],
            response_class=MJPEGStreamingResponse,
        )
    else:

        async def sim_feed_disabled() -> Response:
            return Response(
                content=b"sim_mini not available",
                status_code=503,
                media_type="text/plain",
            )

        app.get("/sim_feed")(sim_feed_disabled)

    # ---------- /sim_feed_status (P7.A fallback 状态 / P7.C 新鲜度) ----------
    @app.get("/sim_feed_status")
    async def sim_feed_status() -> dict[str, object]:
        """前端轮询这个 endpoint 判断显示真视频还是占位图。

        - available=True: 最近 stale_after 秒内拿到过真帧 → UI 显示 <img> 真视频
        - available=False: 超过 stale_after 秒无真帧(从未收到 / 流中断) → UI 显示占位图
          (P7.C:可双向切换,相机恢复后探针 0.5s 内把它翻回 true)
        """
        last = sim_feed_stats["last_frame_at"]
        last_age = None if last is None else round(time.monotonic() - last, 3)
        return {
            "available": _is_available(),
            "frames_received": sim_feed_stats["frames_received"],
            "frames_attempted": sim_feed_stats["frames_attempted"],
            "last_frame_age_sec": last_age,
        }

    # ---------- /scene_feed(场景流 studio_close,640x640)----------
    # 帧源来自 daemon_launcher patch 的 UDP:5006 → SceneUdpReceiver;
    # 探针/占位/available 语义与 /sim_feed 完全复用。
    if scene_provider is not None:
        scene_endpoint = make_mjpeg_endpoint(
            _FrameProviderShim(scene_provider), frame_interval, label="scene",
            stats=scene_feed_stats,
            fallback_jpeg_factory=lambda: generate_placeholder_jpeg(
                "等待场景视频信号\ndaemon studio_close → UDP:5006 链路未就绪\n恢复后自动切回真视频",
                width=640,
                height=640,
            ),
        )
        app.add_api_route(
            "/scene_feed",
            scene_endpoint,
            methods=["GET"],
            response_class=MJPEGStreamingResponse,
        )
    else:

        async def scene_feed_disabled() -> Response:
            return Response(
                content=b"scene_provider not available",
                status_code=503,
                media_type="text/plain",
            )

        app.get("/scene_feed")(scene_feed_disabled)

    # ---------- /scene_feed_status(语义同 /sim_feed_status)----------
    @app.get("/scene_feed_status")
    async def scene_feed_status() -> dict[str, object]:
        """前端轮询:available=True 显示场景真视频,False 显示占位图(双向切换)。"""
        last = scene_feed_stats["last_frame_at"]
        last_age = None if last is None else round(time.monotonic() - last, 3)
        return {
            "available": _is_fresh(scene_feed_stats),
            "frames_received": scene_feed_stats["frames_received"],
            "frames_attempted": scene_feed_stats["frames_attempted"],
            "last_frame_age_sec": last_age,
        }

    # ---------- /camera_feed(真机)----------
    # V2 Fix D:real_frame_provider(如 local_camera.UsbEyeCamera)优先于 real_mini
    # —— 有线版真机相机由 app 直连 v4l2(daemon B --no-media 不开媒体链)。
    # provider 是"懒开"的:相机没插 → get_frame_jpeg 返回 None → 占位图。
    real_src = None
    if real_frame_provider is not None:
        real_src = _FrameProviderShim(real_frame_provider)
    elif real_mini is not None:
        real_src = real_mini

    if real_src is not None:
        app.add_api_route(
            "/camera_feed",
            make_mjpeg_endpoint(
                real_src, frame_interval, label="real",
                fallback_jpeg_factory=lambda: generate_placeholder_jpeg(
                    "真机相机未就绪\nUSB 相机未插入或初始化中\n(real 模式插入真机后自动出现)"
                ),
            ),
            methods=["GET"],
            response_class=MJPEGStreamingResponse,
        )
    else:

        async def camera_feed_disabled() -> Response:
            return Response(
                content=b"real_mini not available (pure_sim mode?)",
                status_code=503,
                media_type="text/plain",
            )

        app.get("/camera_feed")(camera_feed_disabled)

    # ---------- /healthz ----------
    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "sim_available": sim_mini is not None,
            "real_available": real_mini is not None,
            "scene_available": scene_provider is not None,
            "target_fps": target_fps,
        }

    # ---------- /ws/state(V1.1:three.js 3D 视图实时状态通道)----------
    @app.websocket("/ws/state")
    async def ws_state(ws: WebSocket) -> None:
        """25Hz 推送 `{type, ts, head_joints[7], antennas[2], head_pose[16]}`。

        head_pose 是 SDK 原始返回(row-major 4x4 flatten,z 含 -0.177 偏移),
        坐标系修正逻辑统一放前端(与 manifest 的 site 偏移一起处理)。
        sim_mini 读取走 SDK ws_client 本地缓存(copy),高频调用零开销;
        真实新鲜度由 daemon 的 25Hz streaming loop 保证。
        """
        await ws.accept()
        ws_state_clients.add(ws)
        logger.info(f"[ws/state] client connected ({len(ws_state_clients)} total)")
        try:
            while True:
                # 客户端无需上行;receive 只为可靠感知断开
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as e:  # noqa: BLE001 — 连接异常统一按断开处理
            logger.debug(f"[ws/state] receive error: {type(e).__name__}: {e}")
        finally:
            ws_state_clients.discard(ws)
            logger.info(f"[ws/state] client disconnected ({len(ws_state_clients)} left)")

    return app


class _FrameProviderShim:
    """把"只有 get_frame_jpeg()"的帧源(如 SceneUdpReceiver)包装成
    `mini.media.get_frame_jpeg()` 形状,复用探针 / MJPEG / _safe_get_frame_jpeg。

    注意:故意不暴露 `camera` 属性 → _safe_get_frame_jpeg 的 SDK 快速路径
    (camera is None)不会命中,行为与 FakeMedia 一致(属性不存在 = 照常调用)。
    """

    def __init__(self, provider: object) -> None:
        self.media = provider


class MJPEGStreamingResponse(StreamingResponse):
    """StreamingResponse 子类:默认带 MJPEG 的 Content-Type(P7.D fix)。

    为什么需要:FastAPI 的 add_api_route(..., response_class=StreamingResponse)
    会自动包装 async generator endpoint,但**不传 media_type** → 响应头缺
    `Content-Type: multipart/x-mixed-replace; boundary=--frame`,浏览器无法识别
    MJPEG 流 → <img> 黑屏/不渲染。

    用法:
        endpoint = make_mjpeg_endpoint(...)  # async generator function
        app.add_api_route(path, endpoint, methods=["GET"],
                          response_class=MJPEGStreamingResponse)
        # FastAPI 看到 async generator + response_class 是 StreamingResponse 子类,
        # 自动用 MJPEGStreamingResponse(stream(), media_type="multipart/x-mixed-replace; ...")
        # 包装,Content-Type 正确。
    """

    def __init__(self, content, **kwargs):
        kwargs.setdefault("media_type", "multipart/x-mixed-replace; boundary=--frame")
        super().__init__(content, **kwargs)


def make_mjpeg_endpoint(
    reachy_mini: object,
    frame_interval: float,
    *,
    label: str,
    stats: dict | None = None,
    fallback_jpeg_factory=None,
):
    """构造 MJPEG streaming 的 async generator endpoint 函数。

    Bug fix (P7.A):endpoint 必须是 async generator function(不是 StreamingResponse 实例),
    否则 FastAPI 把 Response 实例当 ASGI callable → 422。

    Bug fix (P7.D):endpoint 仍返回 async generator,但路由要用
    `response_class=MJPEGStreamingResponse`(而非 StreamingResponse)——
    这样子类自动带正确的 `Content-Type: multipart/x-mixed-replace` 响应头。

    P7.A fallback:连续多次 None 时发占位图;真帧恢复时自动切回(不粘)。
    """
    FALLBACK_AFTER_N_MISSES = 30  # 30 次 ~ 3 秒无帧后,开始发占位图

    async def stream():
        # MJPEG multipart 边界
        boundary = b"--frame"

        logger.info(f"[camera-stream/{label}] client connected")
        consecutive_misses = 0
        try:
            while True:
                # 拉一帧 JPEG bytes(同步调用,放到 thread pool 避免阻塞 event loop)
                if stats is not None:
                    stats["frames_attempted"] += 1
                jpeg_bytes = await asyncio.to_thread(_safe_get_frame_jpeg, reachy_mini)
                if jpeg_bytes is None:
                    consecutive_misses += 1
                    if (
                        fallback_jpeg_factory is not None
                        and consecutive_misses >= FALLBACK_AFTER_N_MISSES
                    ):
                        # 沙箱缺 webrtc/GL → 用占位图占住画面,UI 提示"沙箱限制"
                        # P7.C:占位只是临时输出 —— 每轮循环仍先尝试真帧,
                        # 真帧恢复时 consecutive_misses 清零,自动切回真视频(不粘)。
                        jpeg_bytes = fallback_jpeg_factory()
                    else:
                        await asyncio.sleep(0.1)
                        continue
                else:
                    consecutive_misses = 0
                    if stats is not None:
                        _record_real_frame(stats)

                # multipart chunk
                chunk = (
                    boundary
                    + b"\r\n"
                    + b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(jpeg_bytes)}\r\n\r\n".encode()
                    + jpeg_bytes
                    + b"\r\n"
                )
                yield chunk
                await asyncio.sleep(frame_interval)
        except asyncio.CancelledError:
            logger.info(f"[camera-stream/{label}] client disconnected")
            raise
        except Exception as e:
            logger.exception(f"[camera-stream/{label}] stream error: {e}")

    return stream


def _record_real_frame(stats: dict) -> None:
    """拿到一帧真 JPEG 时统一更新 stats(探针和 MJPEG 流共用,避免逻辑漂移)。"""
    stats["frames_received"] += 1
    stats["last_frame_at"] = time.monotonic()


# ============================================================================
# V1.1 — /ws/state 状态广播(three.js 3D 视图数据通道)
# ============================================================================
def _read_robot_state(sim_mini: object | None) -> dict[str, object]:
    """从 sim_mini 读一帧状态(SDK ws_client 本地缓存,零 IO)。

    返回可 JSON 序列化的 dict;任一项读取失败/daemon 未推首帧时为 None,
    前端据此显示"等待数据"而不崩。
    head_pose 为 SDK 原始 4x4(row-major flatten),坐标系修正在前端做。
    """
    payload: dict[str, object] = {
        "type": "state",
        "ts": time.monotonic(),
        "head_joints": None,
        "antennas": None,
        "head_pose": None,
    }
    if sim_mini is None:
        return payload
    try:
        head_joints, antennas = sim_mini.get_current_joint_positions()  # type: ignore[attr-defined]
        payload["head_joints"] = [float(v) for v in head_joints]
        payload["antennas"] = [float(v) for v in antennas]
    except Exception as e:  # daemon 首帧未到时 SDK 会 assert
        logger.debug(f"[ws/state] joints not ready: {type(e).__name__}: {e}")
    try:
        import numpy as np

        pose = sim_mini.get_current_head_pose()  # type: ignore[attr-defined]
        payload["head_pose"] = [float(v) for v in np.asarray(pose).flatten().tolist()]
    except Exception as e:
        logger.debug(f"[ws/state] head_pose not ready: {type(e).__name__}: {e}")
    return payload


async def _state_broadcast_loop(
    sim_mini: object | None,
    clients: set[WebSocket],
    *,
    hz: float = 25.0,
) -> None:
    """25Hz(对齐 daemon streaming_timestep=0.04)向所有 /ws/state 客户端广播。

    无客户端时空转 sleep(不读 sim_mini,省 CPU);发送失败的连接立即剔除,
    由 endpoint 侧的 finally 做最终清理(discard 幂等)。
    """
    interval = 1.0 / max(hz, 1.0)
    while True:
        if clients:
            msg = json.dumps(_read_robot_state(sim_mini))
            dead: list[WebSocket] = []
            for ws in list(clients):
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                clients.discard(ws)
        await asyncio.sleep(interval)


async def _frame_probe_loop(
    reachy_mini: object,
    stats: dict,
    interval: float,
) -> None:
    """P7.C 后台探针:与 MJPEG 客户端无关地持续探测相机,刷新 stats。

    为什么需要它:stats 原来只在有浏览器 <img> 连着 /sim_feed 时才更新,
    而 UI 只在 available=true 时才保留 <img> —— 冷启动相机慢几秒就会死锁
    (tick 切占位 → img 断开 → stats 永远冻结 → available 永远 false)。
    探针打破这个循环:available 永远反映相机真实健康度,与是否有客户端无关。

    开销:每 interval 秒(默认 0.5s)一次 get_frame_jpeg(SDK 内部只是读最新
    帧 buffer);camera 未初始化时走 _safe_get_frame_jpeg 快速路径,不触发
    SDK 的 "Camera is not initialized." 警告刷屏。
    """
    while True:
        stats["frames_attempted"] += 1
        jpeg = await asyncio.to_thread(_safe_get_frame_jpeg, reachy_mini)
        if jpeg is not None:
            _record_real_frame(stats)
        await asyncio.sleep(interval)


# sentinel:区分 "media 没有 camera 属性"(测试 Fake)和 "camera 属性为 None"(SDK 未就绪)
_CAMERA_ATTR_MISSING = object()


def _safe_get_frame_jpeg(reachy_mini: object) -> bytes | None:
    """调 reachy_mini.media.get_frame_jpeg(),捕获所有异常返回 None。

    P7.C 快速路径:SDK `MediaManager.get_frame_jpeg()` 在 `camera is None` 时
    每调一次就打一条 `Camera is not initialized.` 警告 —— 启动期相机初始化要
    几秒,期间流生成器/探针高频调用会把日志刷爆。这里先轻量预检:
    camera 属性存在且为 None → 直接返回 None,不调用 SDK(零警告);
    camera 属性不存在(测试里的 FakeMedia)→ 照常调用,行为不变。
    """
    try:
        media = getattr(reachy_mini, "media", None)
        if media is None:
            return None
        camera = getattr(media, "camera", _CAMERA_ATTR_MISSING)
        # _CAMERA_ATTR_MISSING 不是 None,故仅当属性存在且为 None(SDK 未就绪)时命中
        if camera is None:
            return None
        return media.get_frame_jpeg()
    except Exception as e:
        logger.debug(f"_safe_get_frame_jpeg: {type(e).__name__}: {e}")
        return None


def generate_placeholder_jpeg(text: str, width: int = 640, height: int = 360) -> bytes:
    """生成一张带说明文字的占位 JPEG(P7.A fallback 用)。

    Args:
        text: 显示在图片中央的多行文字。
        width, height: 图片尺寸(默认 640x360 16:9,跟 sim 视窗比例接近)。

    Returns:
        JPEG bytes,可用在 MJPEG multipart chunk 里。

    P7.A 用途:沙箱环境缺 GStreamer webrtc plugin + mujoco GL context 时,
    /sim_feed 真视频流不可用。前端轮询 /sim_feed_status 拿 available=false 后,
    显示此占位图 + 文字提示"沙箱限制:真环境会显示 Mujoco 仿真"。
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        # 没 PIL 时返回最小的 1x1 黑色 JPEG (不抛异常,确保 stream 不挂)
        logger.warning("PIL not available, placeholder image disabled")
        return bytes.fromhex(
            "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605"
            "080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20"
            "24"
            "2e2720222c231c1c2837292c30313434341f27393d38323c2e333432ff"
            "c0000b080001000101011100ffc4001f000001050101010101010000000000"
            "0000000102030405060708090affc4001f01000301010101010101010100"
            "00000000000102030405060708090affda0008010100003f00fb0000ffd9"
        )

    # 暗色背景
    img = Image.new("RGB", (width, height), color=(20, 20, 28))
    draw = ImageDraw.Draw(img)

    # 字体:PIL 自带 load_default
    try:
        font_title = ImageFont.load_default(size=24)
        font_body = ImageFont.load_default(size=16)
    except TypeError:
        # 旧版 PIL load_default 不支持 size
        font_title = ImageFont.load_default()
        font_body = ImageFont.load_default()

    lines = text.split("\n")
    line_height = 28
    total_height = line_height * len(lines) + 20
    y = (height - total_height) // 2

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_title if line == lines[0] else font_body)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) // 2
        # 第一行(标题)用醒目颜色,其余用浅灰
        color = (255, 200, 64) if line == lines[0] else (180, 180, 190)
        draw.text((x, y), line, fill=color, font=font_title if line == lines[0] else font_body)
        y += line_height

    # 画边框
    draw.rectangle([(0, 0), (width - 1, height - 1)], outline=(80, 80, 100), width=2)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
