"""P0-1 TTS 自动播放端点测试(/api/tts_state + /api/tts_audio)。

保护的事:
  1. /api/tts_state:无 last_audio_path → available=False, should_play=False。
  2. 有 wav 文件 → available=True, should_play=True(pure_sim), mtime/path 正确。
  3. run_mode=real_plus_sim → should_play=False(TTS 走真机扬声器,
     浏览器再播会双重发声);available 仍为 True(可供 UI 显示)。
  4. last_audio_path 指向已消失文件 → available=False(不 crash)。
  5. /api/tts_audio:有音频 → 200 + audio/wav + 字节内容一致;
     无音频 → 404(前端轮询期间属正常)。
  6. CORS:7860 来源 GET 预检放行(前端从 Gradio 7860 跨源轮询 7861)。

全部用 Fake mini + 临时 wav 文件,不打真 daemon、不合成真 TTS。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from reachymini_conversation.state_bus import get_state_bus, reset_state_bus  # noqa: E402
from reachymini_conversation.utils.camera_stream import (  # noqa: E402
    create_camera_stream_app,
)

# 极小但合法的 WAV 头(RIFF 44 字节,0 数据块)——端点不解析内容,原样转发即可
_FAKE_WAV = (
    b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"\x40\x1f\x00\x00\x80\x3e\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)


@pytest.fixture()
def wav_file(tmp_path: Path):
    """写一个临时 wav,返回路径。"""
    p = tmp_path / "tts_test.wav"
    p.write_bytes(_FAKE_WAV)
    return p


@pytest.fixture()
def client():
    """每个测试干净 state_bus + 无 sim_mini 的最小 app。"""
    reset_state_bus()
    app = create_camera_stream_app(sim_mini=None)
    with TestClient(app) as c:
        yield c
    reset_state_bus()


class TestTtsState:
    def test_no_audio(self, client: TestClient) -> None:
        r = client.get("/api/tts_state")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert body["should_play"] is False
        assert body["path"] is None
        assert body["mtime"] is None

    def test_with_audio_pure_sim(self, client: TestClient, wav_file: Path) -> None:
        get_state_bus().update("last_audio_path", str(wav_file))
        get_state_bus().update("run_mode", "pure_sim")
        body = client.get("/api/tts_state").json()
        assert body["available"] is True
        assert body["should_play"] is True
        assert body["run_mode"] == "pure_sim"
        assert body["path"] == str(wav_file)
        assert isinstance(body["mtime"], float)
        assert body["mtime"] > 0

    def test_real_mode_suppresses_browser_play(
        self, client: TestClient, wav_file: Path
    ) -> None:
        """real_plus_sim:TTS 已路由真机扬声器,浏览器不得自动播(双重发声)。"""
        get_state_bus().update("last_audio_path", str(wav_file))
        get_state_bus().update("run_mode", "real_plus_sim")
        body = client.get("/api/tts_state").json()
        assert body["available"] is True
        assert body["should_play"] is False
        assert body["run_mode"] == "real_plus_sim"

    def test_missing_file(self, client: TestClient, tmp_path: Path) -> None:
        """wav 被系统清理(/tmp 重启会丢)→ available=False,不 500。"""
        get_state_bus().update(
            "last_audio_path", str(tmp_path / "gone.wav")
        )
        body = client.get("/api/tts_state").json()
        assert body["available"] is False
        assert body["should_play"] is False

    def test_mtime_changes_on_rewrite(
        self, client: TestClient, wav_file: Path
    ) -> None:
        """同路径重写(覆盖式更新)也要能被前端 (path, mtime) 检测出变化。"""
        get_state_bus().update("last_audio_path", str(wav_file))
        m1 = client.get("/api/tts_state").json()["mtime"]
        wav_file.write_bytes(_FAKE_WAV + b"\x00" * 8)
        import os

        os.utime(wav_file, (wav_file.stat().st_atime, wav_file.stat().st_mtime + 1))
        m2 = client.get("/api/tts_state").json()["mtime"]
        assert m2 != m1


class TestTtsAudio:
    def test_serve_wav_bytes(self, client: TestClient, wav_file: Path) -> None:
        get_state_bus().update("last_audio_path", str(wav_file))
        r = client.get("/api/tts_audio")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/wav")
        assert r.headers.get("cache-control") == "no-store"
        assert r.content == _FAKE_WAV

    def test_no_audio_404(self, client: TestClient) -> None:
        r = client.get("/api/tts_audio")
        assert r.status_code == 404

    def test_missing_file_404(self, client: TestClient, tmp_path: Path) -> None:
        get_state_bus().update("last_audio_path", str(tmp_path / "gone.wav"))
        assert client.get("/api/tts_audio").status_code == 404


class TestCors:
    def test_preflight_from_gradio_origin(self, client: TestClient) -> None:
        """Gradio(7860)页面跨源 GET 7861 —— 预检必须放行。"""
        r = client.options(
            "/api/tts_state",
            headers={
                "Origin": "http://localhost:7860",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") in (
            "http://localhost:7860",
            "*",
        )
