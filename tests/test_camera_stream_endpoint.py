"""P7.A camera_stream endpoint Bug A 回归测试。

Bug 描述:`camera_stream.py` 原代码 `app.get("/sim_feed")(make_mjpeg_endpoint(...))`,
其中 `make_mjpeg_endpoint` 返回 `StreamingResponse` 实例而非 endpoint 函数。
FastAPI 把 StreamingResponse 当 ASGI callable 注册,期待 query 参数 `receive`/`send`,
导致 GET /sim_feed 返回 422 Missing query。

修法:`make_mjpeg_endpoint` 返回 async generator function,路由用
`add_api_route(path, endpoint, methods=["GET"], response_class=StreamingResponse)`。

本测试必须 catch 任何代码改动破坏这个修复 —— 任何人重新把 StreamingResponse 实例
当 endpoint 注册,本测试立即 fail。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# 一个最小但合法的 JPEG (1x1 白色像素) 的 hex
# 由 Python `PIL.Image.new("RGB",(1,1),(255,255,255)).save("/tmp/x.jpg")` 生成
TINY_JPEG_HEX = (
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707"
    "070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c"
    "1c2837292c30313434341f27393d38323c2e333432ffc0000b0800010001010111"
    "00ffc4001f0000010501010101010100000000000000000102030405060708090a0b"
    "ffc4001f0100030101010101010101010000000000000102030405060708090a0b"
    "ffda0008010100003f00fb0000ffd9"
)
TINY_JPEG = bytes.fromhex(TINY_JPEG_HEX)


class _FakeMedia:
    """FakeMedia: 返回固定 JPEG bytes。"""

    def __init__(self, jpeg: bytes = TINY_JPEG, fail: bool = False) -> None:
        self._jpeg = jpeg
        self._fail = fail

    def get_frame_jpeg(self) -> bytes | None:
        if self._fail:
            raise RuntimeError("simulated failure")
        return self._jpeg


class _FakeMini:
    """FakeReachyMini: 暴露 .media 属性。"""

    def __init__(self, jpeg: bytes = TINY_JPEG, fail: bool = False) -> None:
        self.media = _FakeMedia(jpeg=jpeg, fail=fail)


def _get_sim_feed_route(app):
    """从 FastAPI app 找到 /sim_feed 路由,找不到 raise。"""
    for r in app.routes:
        path = str(getattr(r, "path", ""))
        if path.endswith("/sim_feed"):
            return r
    raise AssertionError("/sim_feed route not found")


def test_sim_feed_endpoint_is_callable_not_streaming_response():
    """核心回归:Bug A — endpoint 必须是 callable function,不是 StreamingResponse 实例。"""
    from fastapi.responses import StreamingResponse

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    app = create_camera_stream_app(sim_mini=_FakeMini(), real_mini=None, target_fps=15)
    route = _get_sim_feed_route(app)
    endpoint = getattr(route, "endpoint", None)

    # 1. 必须存在
    assert endpoint is not None, "/sim_feed endpoint is None"

    # 2. 必须是 callable (function / coroutine function)
    assert callable(endpoint), (
        f"/sim_feed endpoint is not callable, type={type(endpoint).__name__}"
    )

    # 3. 不能是 StreamingResponse 实例 (Bug A 症状)
    assert not isinstance(endpoint, StreamingResponse), (
        "BUG A REGRESSED: /sim_feed endpoint is StreamingResponse instance — "
        "GET will return 422 Missing query receive/send. "
        "Use app.add_api_route(path, async_gen_function, response_class=StreamingResponse) "
        "instead of app.get(path)(StreamingResponse_instance)."
    )


def test_sim_feed_endpoint_function_returns_async_generator():
    """endpoint 是 async generator function(让 FastAPI 用 StreamingResponse 包装)。"""
    import inspect

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    app = create_camera_stream_app(sim_mini=_FakeMini(), real_mini=None, target_fps=15)
    route = _get_sim_feed_route(app)
    endpoint = route.endpoint

    # async def + yield → async generator function
    assert inspect.isasyncgenfunction(endpoint), (
        f"endpoint must be async generator function, got "
        f"{type(endpoint).__name__} (is_coroutinefunction={inspect.iscoroutinefunction(endpoint)})"
    )


def test_sim_feed_yields_valid_mjpeg_chunk():
    """直接调 endpoint 验证输出包含 MJPEG 边界 + 有效 JPEG bytes。"""
    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    app = create_camera_stream_app(sim_mini=_FakeMini(), real_mini=None, target_fps=15)
    route = _get_sim_feed_route(app)
    endpoint = route.endpoint

    async def _read_one_chunk():
        gen = endpoint()
        return await asyncio.wait_for(gen.__anext__(), timeout=3.0)

    chunk = asyncio.run(_read_one_chunk())

    # 必须包含 multipart boundary
    assert b"--frame" in chunk, f"--frame boundary missing in chunk[:200]={chunk[:200]!r}"

    # 必须包含 Content-Type: image/jpeg header
    assert b"Content-Type: image/jpeg" in chunk

    # 必须包含 Content-Length 头
    assert b"Content-Length:" in chunk

    # 必须包含 JPEG SOI marker (0xff 0xd8)
    assert b"\xff\xd8" in chunk, f"JPEG SOI missing in chunk[:200]={chunk[:200]!r}"

    # 必须包含 JPEG EOI marker (0xff 0xd9)
    assert b"\xff\xd9" in chunk, f"JPEG EOI missing in chunk[:200]={chunk[:200]!r}"


def test_sim_feed_handles_media_failure_gracefully():
    """media.get_frame_jpeg() 抛异常时,endpoint 不崩(graceful degradation)。"""
    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    # media 抛 RuntimeError → _safe_get_frame_jpeg 捕获,返回 None → 流继续,只 sleep
    app = create_camera_stream_app(
        sim_mini=_FakeMini(fail=True), real_mini=None, target_fps=15
    )
    route = _get_sim_feed_route(app)
    endpoint = route.endpoint

    async def _read_with_timeout():
        gen = endpoint()
        try:
            # 3 秒内读不到帧(None 帧会一直 sleep 0.1),就 timeout 抛
            return await asyncio.wait_for(gen.__anext__(), timeout=1.5)
        except asyncio.TimeoutError:
            return None  # 表示流在持续运行但无帧(可接受)

    result = asyncio.run(_read_with_timeout())
    # 不抛异常 = pass(None 是 timeout 的预期结果)
    assert result is None or b"--frame" in result


def test_sim_feed_returns_503_when_sim_mini_none():
    """sim_mini=None 时,/sim_feed 返回 503 plain text (不是 422)。"""
    from fastapi.testclient import TestClient

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    app = create_camera_stream_app(sim_mini=None, real_mini=None, target_fps=15)
    client = TestClient(app)
    resp = client.get("/sim_feed")
    assert resp.status_code == 503, f"expected 503, got {resp.status_code}"
    assert b"sim_mini not available" in resp.content


def test_camera_feed_endpoint_is_callable_not_streaming_response():
    """camera_feed (真机) endpoint 也有同样的 Bug A 回归保护。"""
    from fastapi.responses import StreamingResponse

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    app = create_camera_stream_app(sim_mini=None, real_mini=_FakeMini(), target_fps=15)
    for r in app.routes:
        if str(getattr(r, "path", "")).endswith("/camera_feed"):
            endpoint = r.endpoint
            assert callable(endpoint)
            assert not isinstance(endpoint, StreamingResponse), (
                "BUG A REGRESSED on /camera_feed"
            )
            break
    else:
        raise AssertionError("/camera_feed route not found")


def test_healthz_returns_correct_json():
    """/healthz 返回正确的 sim/real availability 信息。"""
    from fastapi.testclient import TestClient

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    # sim 可用,real 不可用
    app = create_camera_stream_app(sim_mini=_FakeMini(), real_mini=None, target_fps=15)
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["sim_available"] is True
    assert body["real_available"] is False
    assert body["target_fps"] == 15


def test_sim_feed_status_endpoint_exists():
    """/sim_feed_status (P7.A) endpoint 存在,返回当前 stats dict。"""
    from fastapi.testclient import TestClient

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    app = create_camera_stream_app(sim_mini=_FakeMini(), real_mini=None, target_fps=15)
    client = TestClient(app)
    resp = client.get("/sim_feed_status")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "available" in body
    assert "frames_received" in body
    assert "frames_attempted" in body
    # 还没人连接过 → frames 都是 0
    assert body["frames_received"] == 0
    assert body["frames_attempted"] == 0
    assert body["available"] is False


def test_sim_feed_status_reports_available_after_frames():
    """P7.A: 真正拉帧后,/sim_feed_status 应该报 available=true。"""
    import asyncio

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    # 用成功的 FakeMini
    app = create_camera_stream_app(sim_mini=_FakeMini(), real_mini=None, target_fps=15)

    # 找到 sim_feed endpoint 调一次
    sim_route = None
    for r in app.routes:
        if str(getattr(r, "path", "")).endswith("/sim_feed"):
            sim_route = r
            break
    assert sim_route is not None

    endpoint = sim_route.endpoint

    async def _read_three_chunks():
        gen = endpoint()
        chunks = []
        try:
            for _ in range(3):
                chunks.append(await asyncio.wait_for(gen.__anext__(), timeout=2.0))
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass
        return chunks

    chunks = asyncio.run(_read_three_chunks())
    assert len(chunks) >= 1, "expected at least 1 MJPEG chunk from FakeMini"

    # 现在查 status,应该 available=true
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/sim_feed_status")
    body = resp.json()
    assert body["frames_received"] >= 1
    assert body["available"] is True


def test_sim_feed_fallback_when_media_always_fails():
    """P7.A: media.get_frame_jpeg() 永远返回 None 时,endpoint 改用占位图。"""
    import asyncio

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    # fail=True 的 FakeMini
    app = create_camera_stream_app(sim_mini=_FakeMini(fail=True), real_mini=None, target_fps=15)

    # 找 endpoint
    sim_route = None
    for r in app.routes:
        if str(getattr(r, "path", "")).endswith("/sim_feed"):
            sim_route = r
            break
    endpoint = sim_route.endpoint

    # 连续读 5 帧(应该都被 fallback 占位图填充)
    async def _read_five_chunks():
        gen = endpoint()
        chunks = []
        try:
            for _ in range(5):
                chunks.append(await asyncio.wait_for(gen.__anext__(), timeout=3.0))
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass
        return chunks

    chunks = asyncio.run(_read_five_chunks())
    assert len(chunks) >= 1, "expected fallback chunks even when media fails"

    # 每个 chunk 应该有 JPEG SOI + EOI (fallback 也是 JPEG)
    for c in chunks:
        assert b"\xff\xd8" in c, f"missing JPEG SOI in fallback chunk {c[:64]!r}"
        assert b"\xff\xd9" in c, f"missing JPEG EOI in fallback chunk {c[:64]!r}"

    # status 应该报 frames_received=0 + frames_attempted > 30
    from fastapi.testclient import TestClient

    client = TestClient(app)
    body = client.get("/sim_feed_status").json()
    assert body["frames_received"] == 0
    assert body["frames_attempted"] >= 30, f"expected >=30 misses, got {body['frames_attempted']}"
    assert body["available"] is False


def test_generate_placeholder_jpeg_returns_valid_jpeg():
    """generate_placeholder_jpeg 应该返回有效的 JPEG bytes。"""
    from reachymini_conversation.utils.camera_stream import generate_placeholder_jpeg

    jpeg = generate_placeholder_jpeg("TEST PLACEHOLDER\nLine 2")
    assert isinstance(jpeg, bytes)
    assert len(jpeg) > 100, f"JPEG too small ({len(jpeg)} bytes)"
    # JPEG SOI + EOI markers
    assert jpeg[:2] == b"\xff\xd8", f"missing JPEG SOI: {jpeg[:4].hex()}"
    assert jpeg[-2:] == b"\xff\xd9", f"missing JPEG EOI: {jpeg[-4:].hex()}"


def test_generate_placeholder_jpeg_handles_pil_missing():
    """PIL 不可用时,generate_placeholder_jpeg 应该返回 fallback (不抛异常)。"""
    import sys

    from reachymini_conversation.utils import camera_stream

    # Monkey-patch 让 PIL import 失败
    orig_pil = sys.modules.get("PIL")
    sys.modules["PIL"] = None  # 让 import 抛 ImportError
    try:
        jpeg = camera_stream.generate_placeholder_jpeg("fallback test")
        assert isinstance(jpeg, bytes)
        assert jpeg[:2] == b"\xff\xd8"
        assert jpeg[-2:] == b"\xff\xd9"
    finally:
        if orig_pil is not None:
            sys.modules["PIL"] = orig_pil


# ============================================================================
# P7.C guard:粘性 fallback / 慢初始化 race 修复的回归测试
#
# Bug 背景(MJPEG 流 Content-Type 缺失导致浏览器黑屏):
#   stats 原来只在 MJPEG 流生成器里更新 → 必须有浏览器 <img> 连着 /sim_feed
#   才统计;而 UI tick 看到 available=false 就把 <img> 换成占位 HTML → 客户端
#   断开 → 相机 ready 后也没客户端 → frames_received 永远 0 → UI 永久占位。
#   反向:available = frames_received > 0 单调粘 true,流断了 UI 仍显示裂图。
# 修复:后台探针(lifespan 启动,与客户端无关)+ available 新鲜度语义(双向)
#   + camera=None 快速路径(消除 SDK 警告刷屏)。
# ============================================================================


class _ScriptedMedia:
    """脚本化 media:先返回 None none_count 次,之后返回真帧;可中途翻成永远 None。

    注意:故意不带 `camera` 属性 → _safe_get_frame_jpeg 的快速路径不会命中,
    行为与修复前一致(属性不存在 = 照常调用)。
    """

    def __init__(self, none_count: int, jpeg: bytes = TINY_JPEG) -> None:
        self._none_left = none_count
        self._jpeg = jpeg
        self.calls = 0
        self.always_none = False

    def get_frame_jpeg(self) -> bytes | None:
        self.calls += 1
        if self.always_none:
            return None
        if self._none_left > 0:
            self._none_left -= 1
            return None
        return self._jpeg


class _ScriptedMini:
    def __init__(self, media: _ScriptedMedia) -> None:
        self.media = media


def _poll_status_until(client, predicate, timeout: float = 5.0) -> dict:
    """轮询 /sim_feed_status 直到 predicate(body) 为真或超时,返回最后一次 body。"""
    import time as _time

    deadline = _time.monotonic() + timeout
    body = client.get("/sim_feed_status").json()
    while _time.monotonic() < deadline:
        body = client.get("/sim_feed_status").json()
        if predicate(body):
            break
        _time.sleep(0.05)
    return body


def test_probe_recovers_available_without_any_client():
    """P7.C 核心 guard:先 None 若干次后恢复出帧,全程无 /sim_feed 客户端,
    available 必须能 false→true 恢复(粘性 fallback 死锁的回归保护)。

    恢复时延上界 ≈ 脚本 none_count × probe_interval = 10 × 0.05s = 0.5s。
    """
    from fastapi.testclient import TestClient

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    media = _ScriptedMedia(none_count=10)
    app = create_camera_stream_app(
        sim_mini=_ScriptedMini(media),
        real_mini=None,
        target_fps=15,
        probe_interval=0.05,  # 测试加速;生产默认 0.5s
    )
    # `with TestClient` 才跑 lifespan → 启动后台探针(老测试无 with,行为不变)
    with TestClient(app) as client:
        first = client.get("/sim_feed_status").json()
        body = _poll_status_until(client, lambda b: b["available"], timeout=5.0)
        assert body["available"] is True, (
            f"available 未在无客户端情况下恢复(粘性 fallback 复发?): "
            f"first={first} last={body} calls={media.calls}"
        )
        assert body["frames_received"] >= 1
        # 全程没人连过 /sim_feed,frames_received 的增长只能来自后台探针
        assert media.calls >= 10


def test_available_goes_stale_after_frames_stop():
    """P7.C guard:帧停止后 available 从 true 过期回 false(双向切换,不粘 true)。"""
    import time as _time

    from fastapi.testclient import TestClient

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    media = _ScriptedMedia(none_count=0)  # 立即出帧
    app = create_camera_stream_app(
        sim_mini=_ScriptedMini(media),
        real_mini=None,
        target_fps=15,
        probe_interval=0.05,
        stale_after=0.3,  # 测试加速;生产默认 2.0s
    )
    with TestClient(app) as client:
        body = _poll_status_until(client, lambda b: b["available"], timeout=5.0)
        assert body["available"] is True, f"前置条件失败:available 应为 true, got {body}"

        # 模拟相机断流:探针继续尝试但永远 None
        media.always_none = True
        _time.sleep(0.6)  # > stale_after

        body = client.get("/sim_feed_status").json()
        assert body["available"] is False, (
            f"断流 {body.get('last_frame_age_sec')}s 后 available 仍 true(粘 true 复发?)"
        )
        assert body["last_frame_age_sec"] >= 0.3
        # 计数仍在涨 = 探针没死,相机恢复后 available 会翻回 true
        assert body["frames_attempted"] > 0

        # 恢复出帧 → available 必须翻回 true(true→false→true 全链路双向)
        media.always_none = False
        body = _poll_status_until(client, lambda b: b["available"], timeout=5.0)
        assert body["available"] is True, f"恢复后 available 未翻回 true: {body}"


def test_sim_feed_switches_from_placeholder_to_real_frames():
    """P7.C guard:/sim_feed 流内 占位图 → 真帧 自动切换(占位只是临时输出,不粘)。"""
    import asyncio

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    # 35 次 None > FALLBACK_AFTER_N_MISSES(30)→ 先进占位模式,之后恢复真帧
    media = _ScriptedMedia(none_count=35)
    app = create_camera_stream_app(
        sim_mini=_ScriptedMini(media), real_mini=None, target_fps=15
    )
    sim_route = None
    for r in app.routes:
        if str(getattr(r, "path", "")).endswith("/sim_feed"):
            sim_route = r
            break
    assert sim_route is not None
    endpoint = sim_route.endpoint

    async def _read_chunks(n: int) -> list[bytes]:
        gen = endpoint()
        chunks = []
        try:
            for _ in range(n):
                chunks.append(await asyncio.wait_for(gen.__anext__(), timeout=6.0))
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass
        return chunks

    chunks = asyncio.run(_read_chunks(8))
    # 时序:第 30~35 次调用发占位(6 帧,每次间隔 frame_interval),
    # 第 36 次起出真帧 → 8 帧里应有 6 占位 + 2 真帧。
    assert len(chunks) >= 2, f"chunks 不足: {len(chunks)}"

    def _is_real(chunk: bytes) -> bool:
        return TINY_JPEG in chunk

    def _is_placeholder(chunk: bytes) -> bool:
        return b"\xff\xd8" in chunk and not _is_real(chunk)

    # 前面的 chunk 必须是占位图(640x360 PIL 生成,不含 TINY_JPEG)
    assert _is_placeholder(chunks[0]), f"首 chunk 应为占位图: {chunks[0][:64]!r}"
    # 流恢复后必须出现真帧,且真帧之后不再回退占位
    real_idx = next((i for i, c in enumerate(chunks) if _is_real(c)), None)
    assert real_idx is not None, "恢复后未出现真帧(占位粘性复发?)"
    assert all(_is_real(c) for c in chunks[real_idx:]), "真帧之后又回退到占位图"

    # 占位图确实是 640x360 的 PIL 占位 JPEG(区别于真帧)
    import io as _io

    from PIL import Image as _Image

    m = chunks[0].find(b"\xff\xd8")
    e = chunks[0].rfind(b"\xff\xd9")
    im = _Image.open(_io.BytesIO(chunks[0][m : e + 2]))
    assert im.size == (640, 360), f"占位图尺寸异常: {im.size}"


def test_camera_none_fastpath_skips_sdk_call():
    """P7.C guard:media.camera is None 时 _safe_get_frame_jpeg 直接返回 None,
    不调用 get_frame_jpeg —— SDK 在该状态下每调一次打一条
    'Camera is not initialized.' 警告,此 guard 保护警告刷屏修复。
    """
    from reachymini_conversation.utils.camera_stream import _safe_get_frame_jpeg

    class _MediaNotReady:
        camera = None  # SDK MediaManager 未就绪状态

        def __init__(self) -> None:
            self.calls = 0

        def get_frame_jpeg(self) -> bytes:
            self.calls += 1
            return TINY_JPEG

    class _Mini:
        def __init__(self, media) -> None:
            self.media = media

    media = _MediaNotReady()
    assert _safe_get_frame_jpeg(_Mini(media)) is None
    assert media.calls == 0, "camera=None 时不应调 get_frame_jpeg(每次调用都刷 SDK 警告)"

    # camera 就绪后正常调用,行为不变
    media.camera = object()
    assert _safe_get_frame_jpeg(_Mini(media)) == TINY_JPEG
    assert media.calls == 1
