"""/scene_feed 场景流端点的 guard 测试(仿 test_camera_stream_endpoint.py)。

保护的事:
  1. /scene_feed 必须注册为 async generator endpoint(不是 StreamingResponse 实例,
     否则重蹈 P7.A Bug A:FastAPI 把 Response 实例当 ASGI callable → 422)。
  2. 有帧时 chunk 是合法 MJPEG multipart + JPEG SOI/EOI。
  3. 无帧时走 640x640 占位图("等待场景视频信号"),available=false,不 crash。
  4. 探针恢复:provider 先 None 后出帧,全程无 /scene_feed 客户端,
     /scene_feed_status 的 available 必须能 false→true(粘性 fallback 回归保护)。
  5. scene_provider.start() 抛异常 → 优雅降级,不 crash app。
  6. scene_provider=None → /scene_feed 503(与 /sim_feed 的禁用语义一致)。

全部用假 provider,不打真 GStreamer / UDP。
"""

from __future__ import annotations

import asyncio
import sys
import time as _time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from test_camera_stream_endpoint import TINY_JPEG  # noqa: E402  复用最小合法 JPEG


class _FakeSceneProvider:
    """假场景帧源:get_frame_jpeg() 返回固定 JPEG;start/stop 记录调用。"""

    def __init__(self, jpeg: bytes | None = TINY_JPEG) -> None:
        self._jpeg = jpeg
        self.started = False
        self.stopped = False

    def start(self) -> bool:
        self.started = True
        return True

    def stop(self) -> None:
        self.stopped = True

    def get_frame_jpeg(self) -> bytes | None:
        return self._jpeg


class _ScriptedSceneProvider:
    """脚本化帧源:先 None none_count 次,之后返回真帧(探针恢复测试用)。"""

    def __init__(self, none_count: int, jpeg: bytes = TINY_JPEG) -> None:
        self._none_left = none_count
        self._jpeg = jpeg
        self.calls = 0

    def start(self) -> bool:
        return True

    def stop(self) -> None:
        pass

    def get_frame_jpeg(self) -> bytes | None:
        self.calls += 1
        if self._none_left > 0:
            self._none_left -= 1
            return None
        return self._jpeg


def _get_route(app, path_suffix: str):
    for r in app.routes:
        if str(getattr(r, "path", "")).endswith(path_suffix):
            return r
    raise AssertionError(f"{path_suffix} route not found")


def test_scene_feed_endpoint_is_async_gen_not_streaming_response():
    """P7.A Bug A 回归保护(scene 版):endpoint 必须是 async generator function。"""
    import inspect

    from fastapi.responses import StreamingResponse

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    app = create_camera_stream_app(
        sim_mini=None, real_mini=None, scene_provider=_FakeSceneProvider()
    )
    endpoint = _get_route(app, "/scene_feed").endpoint
    assert callable(endpoint)
    assert not isinstance(endpoint, StreamingResponse), "BUG A REGRESSED on /scene_feed"
    assert inspect.isasyncgenfunction(endpoint)


def test_scene_feed_yields_valid_mjpeg_chunk():
    """有帧时:chunk 含 MJPEG boundary + JPEG SOI/EOI。"""
    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    app = create_camera_stream_app(
        sim_mini=None, real_mini=None, scene_provider=_FakeSceneProvider()
    )
    endpoint = _get_route(app, "/scene_feed").endpoint

    async def _read_one():
        gen = endpoint()
        return await asyncio.wait_for(gen.__anext__(), timeout=3.0)

    chunk = asyncio.run(_read_one())
    assert b"--frame" in chunk
    assert b"Content-Type: image/jpeg" in chunk
    assert b"\xff\xd8" in chunk and b"\xff\xd9" in chunk
    assert TINY_JPEG in chunk


def test_scene_feed_returns_503_when_provider_none():
    """scene_provider=None → 503(不是 422/500)。"""
    from fastapi.testclient import TestClient

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    app = create_camera_stream_app(sim_mini=None, real_mini=None, scene_provider=None)
    client = TestClient(app)
    resp = client.get("/scene_feed")
    assert resp.status_code == 503
    assert b"scene_provider not available" in resp.content


def test_scene_feed_status_fields_and_default():
    """/scene_feed_status 返回与 sim 版相同的字段;初始 available=false。"""
    from fastapi.testclient import TestClient

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    app = create_camera_stream_app(
        sim_mini=None, real_mini=None, scene_provider=_FakeSceneProvider()
    )
    client = TestClient(app)
    resp = client.get("/scene_feed_status")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("available", "frames_received", "frames_attempted", "last_frame_age_sec"):
        assert key in body, f"status 缺字段 {key}: {body}"
    assert body["available"] is False  # 无 lifespan(未 with)→ 无探针 → 无帧


def test_scene_feed_placeholder_is_640x640_when_no_frames():
    """无帧时:fallback 占位图存在且是 640x640(studio_close 尺寸,方图)。"""
    import io as _io

    from PIL import Image as _Image

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    app = create_camera_stream_app(
        sim_mini=None, real_mini=None, scene_provider=_FakeSceneProvider(jpeg=None)
    )
    endpoint = _get_route(app, "/scene_feed").endpoint

    async def _read_chunks(n: int) -> list[bytes]:
        gen = endpoint()
        chunks = []
        try:
            for _ in range(n):
                chunks.append(await asyncio.wait_for(gen.__anext__(), timeout=8.0))
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass
        return chunks

    # 30 次 miss 后进占位模式;流生成器每次 miss sleep 0.1s → 3s+ 后出占位
    chunks = asyncio.run(_read_chunks(3))
    assert len(chunks) >= 1, "无帧时也应出占位图 chunk"
    chunk = chunks[0]
    assert b"\xff\xd8" in chunk and b"\xff\xd9" in chunk
    m = chunk.find(b"\xff\xd8")
    e = chunk.rfind(b"\xff\xd9")
    im = _Image.open(_io.BytesIO(chunk[m : e + 2]))
    assert im.size == (640, 640), f"场景占位图应为 640x640,实际 {im.size}"


def test_scene_probe_recovers_available_without_client():
    """探针恢复(scene 版 P7.C):先 None 后出帧,无客户端也能 false→true。"""
    from fastapi.testclient import TestClient

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    provider = _ScriptedSceneProvider(none_count=10)
    app = create_camera_stream_app(
        sim_mini=None,
        real_mini=None,
        scene_provider=provider,
        probe_interval=0.05,  # 测试加速;生产默认 0.5s
    )
    with TestClient(app) as client:  # with → 跑 lifespan → 起探针 + start()
        deadline = _time.monotonic() + 5.0
        body = client.get("/scene_feed_status").json()
        while _time.monotonic() < deadline:
            body = client.get("/scene_feed_status").json()
            if body["available"]:
                break
            _time.sleep(0.05)
        assert body["available"] is True, f"available 未恢复(粘性 fallback 复发?): {body}"
        assert body["frames_received"] >= 1
        assert provider.calls >= 10
    # lifespan 退出后 provider.stop() 被调(生命周期托管)
    # (_ScriptedSceneProvider.stop 是 no-op,这里只验证不抛异常)


def test_scene_provider_start_failure_degrades_gracefully():
    """start() 抛异常(模拟 gst 元素缺失)→ 不 crash,status available=false。"""

    class _BoomProvider:
        def start(self) -> bool:
            raise RuntimeError("gst elements missing")

        def stop(self) -> None:
            pass

        def get_frame_jpeg(self) -> bytes | None:
            return None

    from fastapi.testclient import TestClient

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    app = create_camera_stream_app(
        sim_mini=None, real_mini=None, scene_provider=_BoomProvider()
    )
    with TestClient(app) as client:
        resp = client.get("/scene_feed_status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        # 探针仍在跑(frames_attempted 会涨)→ provider 恢复后可自愈
        assert "frames_attempted" in body


def test_scene_receiver_pipeline_string_matches_sender_caps():
    """guard:接收管线 caps 必须覆盖发送端(rtpvrawpay RAW/RGB/payload 96)关键字段。

    防漂移:daemon_launcher 发送 640x640 RGB raw over RTP(payload 96);
    若未来改接收 caps,此测试强制核对与发送端一致。
    """
    from reachymini_conversation.utils.scene_stream import _SCENE_PIPELINE_TEMPLATE

    desc = _SCENE_PIPELINE_TEMPLATE.format(port=5006, width=640, height=640)
    for token in (
        "udpsrc port=5006",
        "encoding-name=(string)RAW",
        "sampling=(string)RGB",
        "payload=(int)96",
        "width=(string)640",
        "height=(string)640",
        "rtpvrawdepay",
        "jpegenc",
        "appsink",
    ):
        assert token in desc, f"管线缺关键字段 {token}: {desc}"
