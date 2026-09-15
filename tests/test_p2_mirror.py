"""P2 MirrorOrchestrator + camera_stream unit tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _make_mini():
    """返回 mock ReachyMini:动作方法是 async mock(MagicMock + side_effect)。"""

    m = MagicMock()

    async def _noop(*args, **kwargs):
        return None

    for name in ("goto_target", "set_target", "play_move", "look_at_image"):
        # 保留 MagicMock(有 .called),side_effect 让调用返回 awaitable
        getattr(m, name).side_effect = _noop
    return m


def test_mirror_pure_sim():
    """pure_sim: only sim is called."""
    from reachymini_conversation.mirror_orchestrator import MirrorOrchestrator

    sim = _make_mini()
    orch = MirrorOrchestrator(sim_mini=sim, real_mini=None, run_mode="pure_sim")
    asyncio.run(orch.goto_target(head=None, duration=0.5))
    assert sim.goto_target.called


def test_mirror_real_plus_sim():
    """real_plus_sim: both sim and real are called."""
    from reachymini_conversation.mirror_orchestrator import MirrorOrchestrator

    sim = _make_mini()
    real = _make_mini()
    orch = MirrorOrchestrator(sim_mini=sim, real_mini=real, run_mode="real_plus_sim")
    asyncio.run(orch.set_target(body_yaw=0.0))
    assert sim.set_target.called
    assert real.set_target.called


def test_mirror_downgrade():
    """run_mode=real_plus_sim but real missing -> downgrade to pure_sim."""
    from reachymini_conversation.mirror_orchestrator import make_orchestrator

    sim = _make_mini()
    orch = make_orchestrator(sim_mini=sim, real_mini=None, run_mode="real_plus_sim")
    assert orch.run_mode == "pure_sim"
    assert orch.real_mini is None


def test_mirror_look_at_image():
    from reachymini_conversation.mirror_orchestrator import MirrorOrchestrator

    sim = _make_mini()
    orch = MirrorOrchestrator(sim_mini=sim)
    asyncio.run(orch.look_at_image(u=160.0, v=120.0, duration=0.3))
    assert sim.look_at_image.called


def test_mirror_partial_failure():
    """Real raises exception: sim still runs; not blocking."""
    from reachymini_conversation.mirror_orchestrator import MirrorOrchestrator

    sim = _make_mini()

    async def _boom(*args, **kwargs):
        raise RuntimeError("sim boom")

    real = _make_mini()

    def sync_boom(*args, **kwargs):
        raise RuntimeError("sim boom")

    real.goto_target.side_effect = sync_boom

    orch = MirrorOrchestrator(sim_mini=sim, real_mini=real, run_mode="real_plus_sim")
    # Should not raise (logged as warning)
    asyncio.run(orch.goto_target())


def test_camera_stream_routes():
    """FastAPI app has /sim_feed /camera_feed /healthz."""
    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    sim = MagicMock()
    sim.media.get_frame_jpeg.return_value = b"\xff\xd8\xff\xe0fake\xff\xd9"
    app = create_camera_stream_app(sim_mini=sim, target_fps=15)
    routes = {r.path for r in app.routes}
    assert "/sim_feed" in routes
    assert "/camera_feed" in routes
    assert "/healthz" in routes


def test_camera_stream_no_sim_returns_503():
    """No sim: /sim_feed returns 503."""
    from fastapi.testclient import TestClient

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    app = create_camera_stream_app(sim_mini=None)
    client = TestClient(app)
    resp = client.get("/sim_feed")
    assert resp.status_code == 503


def test_camera_stream_healthz_ok():
    """Healthz endpoint works."""
    from fastapi.testclient import TestClient

    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    sim = MagicMock()
    app = create_camera_stream_app(sim_mini=sim, target_fps=20)
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["sim_available"] is True
    assert data["real_available"] is False
    assert data["target_fps"] == 20


def test_safe_get_frame_jpeg_handles_exception():
    """If reachy_mini.media throws, return None gracefully."""
    from reachymini_conversation.utils.camera_stream import _safe_get_frame_jpeg

    bad = MagicMock()
    bad.media.get_frame_jpeg.side_effect = RuntimeError("boom")
    assert _safe_get_frame_jpeg(bad) is None

    no_media = MagicMock(spec=[])  # no .media attribute
    assert _safe_get_frame_jpeg(no_media) is None

    good = MagicMock()
    good.media.get_frame_jpeg.return_value = b"\xff\xd8fake\xff\xd9"
    assert _safe_get_frame_jpeg(good) == b"\xff\xd8fake\xff\xd9"
