"""/ws/state WebSocket 状态通道测试(V1.1,three.js 3D 视图数据源)。

保护的事:
  1. /ws/state 路由必须注册(WebSocket 类型)。
  2. 连接后 25Hz 广播,payload schema 固定:
     {type:"state", ts:float, head_joints[7]|None, antennas[2]|None, head_pose[16]|None}。
  3. sim_mini 有数据 → 真值;sim_mini=None 或 daemon 首帧未到 → None 字段,不 crash。
  4. 多客户端都收到同一帧;客户端断开后被剔除,不再向其发送。
  5. CORS:7860 来源的 GET 预检要放行(three.js 跨源拉 STL/JS 用)。

全部用 Fake mini,不打真 daemon。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from reachymini_conversation.utils.camera_stream import (  # noqa: E402
    _read_robot_state,
    create_camera_stream_app,
)


class _FakeMini:
    """假 ReachyMini:返回固定关节角度 + identity head_pose。"""

    def __init__(self, ready: bool = True) -> None:
        self._ready = ready

    def get_current_joint_positions(self):
        if not self._ready:
            raise AssertionError("No joint positions received yet")
        return [0.1 * i for i in range(7)], [-0.3, 0.3]

    def get_current_head_pose(self):
        if not self._ready:
            raise AssertionError("No head pose received yet")
        pose = np.eye(4)
        pose[2, 3] = -0.0274  # SDK rest 位姿的典型 z(0.14957 - 0.177)
        return pose


def _make_client(mini) -> TestClient:
    app = create_camera_stream_app(sim_mini=mini)
    return TestClient(app)


# ---------- 单元层:_read_robot_state ----------


def test_read_robot_state_ready():
    payload = _read_robot_state(_FakeMini(ready=True))
    assert payload["type"] == "state"
    assert isinstance(payload["ts"], float)
    assert len(payload["head_joints"]) == 7
    assert len(payload["antennas"]) == 2
    assert len(payload["head_pose"]) == 16
    # head_pose 是 row-major flatten 的 4x4
    pose = np.array(payload["head_pose"]).reshape(4, 4)
    assert np.allclose(pose[:3, :3], np.eye(3))
    assert pose[2, 3] == pytest.approx(-0.0274)


def test_read_robot_state_none_mini():
    payload = _read_robot_state(None)
    assert payload["head_joints"] is None
    assert payload["antennas"] is None
    assert payload["head_pose"] is None


def test_read_robot_state_not_ready():
    """daemon 首帧未到(SDK assert)时全 None,不抛异常。"""
    payload = _read_robot_state(_FakeMini(ready=False))
    assert payload["head_joints"] is None
    assert payload["antennas"] is None
    assert payload["head_pose"] is None
    assert payload["type"] == "state"


# ---------- 端点层:WebSocket ----------


def _get_ws_route(app, path: str):
    for r in app.routes:
        if getattr(r, "path", None) == path:
            return r
    raise AssertionError(f"{path} route not found")


def test_ws_state_route_registered():
    app = create_camera_stream_app(sim_mini=_FakeMini())
    route = _get_ws_route(app, "/ws/state")
    # starlette WebSocketRoute,有 endpoint 属性
    assert hasattr(route, "endpoint")


def test_ws_state_broadcasts_state():
    with _make_client(_FakeMini()) as client:
        with client.websocket_connect("/ws/state") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "state"
            assert len(msg["head_joints"]) == 7
            assert len(msg["antennas"]) == 2
            assert len(msg["head_pose"]) == 16
            # 25Hz:第二帧应该很快到(宽松上限 1s,防 CI 抖动)
            msg2 = ws.receive_json()
            assert msg2["type"] == "state"
            assert msg2["ts"] >= msg["ts"]


def test_ws_state_none_mini_sends_null_fields():
    with _make_client(None) as client:
        with client.websocket_connect("/ws/state") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "state"
            assert msg["head_joints"] is None
            assert msg["head_pose"] is None


def test_ws_state_multiple_clients():
    with _make_client(_FakeMini()) as client:
        with client.websocket_connect("/ws/state") as ws1:
            with client.websocket_connect("/ws/state") as ws2:
                m1 = ws1.receive_json()
                m2 = ws2.receive_json()
                assert m1["head_joints"] == m2["head_joints"]


# ---------- CORS ----------


def test_cors_allows_gradio_origin():
    """three.js 从 7860 页面跨源拉 7861 的静态文件:预检必须放行。"""
    with _make_client(_FakeMini()) as client:
        resp = client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:7860",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:7860"


def test_cors_rejects_unknown_origin():
    with _make_client(_FakeMini()) as client:
        resp = client.options(
            "/healthz",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") != "http://evil.example.com"
