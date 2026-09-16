"""RealVoiceLoop + TTS 音频路由的测试(V2.4)。

保护的事:
  1. TTS 路由:pure_sim → 推 sim;real_plus_sim+real 在线 → 推真机;
     orch 缺失/real 掉线 → 不崩、静默降级。
  2. RealVoiceLoop 激活门槛:仅当 real_plus_sim + voice 模式 + real 在线才采音。
  3. 端到端(假件):假真机麦克风出"一句语音" → pipeline.run_audio 被调一次,
     内容是非空 PCM。
  不碰真硬件/真网络:pipeline 用 Fake。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from reachymini_conversation.mirror_orchestrator import MirrorOrchestrator  # noqa: E402
from reachymini_conversation.real_voice import RealVoiceLoop, make_tts_audio_router  # noqa: E402
from reachymini_conversation.voice_loop import EnergyVAD  # noqa: E402
from reachymini_conversation.state_bus import get_state_bus, reset_state_bus  # noqa: E402


class _FakeMedia:
    def __init__(self) -> None:
        self.pushed: list[np.ndarray] = []

    def push_audio_sample(self, pcm) -> None:
        self.pushed.append(np.asarray(pcm))


class _FakeMini:
    def __init__(self) -> None:
        self.media = _FakeMedia()


@pytest.fixture(autouse=True)
def _clean_bus():
    reset_state_bus()
    yield
    reset_state_bus()


# ---------- TTS 路由 ----------


def test_router_routes_sim_by_default():
    sim, real = _FakeMini(), _FakeMini()
    orch = MirrorOrchestrator(sim_mini=sim, real_mini=None, run_mode="pure_sim")
    route = make_tts_audio_router(lambda: orch)
    route(np.zeros(1600, dtype=np.float32))
    assert len(sim.media.pushed) == 1
    assert len(real.media.pushed) == 0


def test_router_routes_real_when_attached():
    sim, real = _FakeMini(), _FakeMini()
    orch = MirrorOrchestrator(sim_mini=sim, real_mini=real, run_mode="real_plus_sim")
    get_state_bus().update("real_conn_type", "wireless")  # 无线:走 real_mini.media
    route = make_tts_audio_router(lambda: orch)
    route(np.zeros(1600, dtype=np.float32))
    assert len(real.media.pushed) == 1, "无线 real 模式 TTS 必须推 real_mini.media"
    assert len(sim.media.pushed) == 0


def test_router_wired_uses_local_audio(monkeypatch):
    """有线 real 模式:TTS 推本机 USB 声卡(local_audio),不走 real_mini.media。"""
    sim, real = _FakeMini(), _FakeMini()
    orch = MirrorOrchestrator(sim_mini=sim, real_mini=real, run_mode="real_plus_sim")
    get_state_bus().update("real_conn_type", "wired")

    fake_audio = _FakeMedia()
    fake_audio.start_playing = lambda: None
    import reachymini_conversation.local_audio as la

    monkeypatch.setattr(la, "get_local_audio", lambda: fake_audio)
    route = make_tts_audio_router(lambda: orch)
    route(np.zeros(1600, dtype=np.float32))
    assert len(fake_audio.pushed) == 1, "有线模式必须推本机声卡"
    assert len(real.media.pushed) == 0 and len(sim.media.pushed) == 0


def test_router_wired_no_soundcard_falls_back_silently(monkeypatch):
    """有线但本机声卡不在 → 不崩、不推 real_mini.media。"""
    sim, real = _FakeMini(), _FakeMini()
    orch = MirrorOrchestrator(sim_mini=sim, real_mini=real, run_mode="real_plus_sim")
    get_state_bus().update("real_conn_type", "wired")
    import reachymini_conversation.local_audio as la

    monkeypatch.setattr(la, "get_local_audio", lambda: None)
    route = make_tts_audio_router(lambda: orch)
    route(np.zeros(1600, dtype=np.float32))  # 不崩
    assert len(real.media.pushed) == 0


def test_router_no_orch_no_crash():
    route = make_tts_audio_router(lambda: None)
    route(np.zeros(10, dtype=np.float32))  # 不崩即可


def test_router_real_push_failure_degrades():
    class _BadMedia:
        def push_audio_sample(self, pcm):
            raise RuntimeError("audio device gone")

    class _BadMini:
        media = _BadMedia()

    orch = MirrorOrchestrator(sim_mini=_FakeMini(), real_mini=_BadMini(), run_mode="real_plus_sim")
    route = make_tts_audio_router(lambda: orch)
    route(np.zeros(10, dtype=np.float32))  # 异常被吞,不炸轮次


# ---------- RealVoiceLoop 激活门槛 ----------


def _mk_loop(run_mode: str, chat_mode: str, with_real: bool):
    sim, real = _FakeMini(), _FakeMini()
    orch = MirrorOrchestrator(
        sim_mini=sim, real_mini=real if with_real else None, run_mode=run_mode
    )
    bus = get_state_bus()
    bus.update("chat_mode", chat_mode)
    loop = RealVoiceLoop(orch, pipeline=None, idle_sleep_s=0.05)
    return loop, bus


def test_should_listen_gating():
    loop, _ = _mk_loop("real_plus_sim", "voice", True)
    assert loop._should_listen(get_state_bus()) is True

    loop, _ = _mk_loop("pure_sim", "voice", False)  # sim 模式:不用真机麦
    assert loop._should_listen(get_state_bus()) is False

    loop, _ = _mk_loop("real_plus_sim", "text", True)  # 文本模式:不采音
    assert loop._should_listen(get_state_bus()) is False

    loop, _ = _mk_loop("real_plus_sim", "voice", False)  # real 掉线:不采
    assert loop._should_listen(get_state_bus()) is False


# ---------- 端到端(假件)----------


class _FakeRealMiniWithAudio(_FakeMini):
    """假真机:媒体给一段"静音+语音+静音"脚本化音频,触发 VAD 出句。"""

    SR = 16000

    class _ScriptedMedia(_FakeMedia):
        SR = 16000

        def __init__(self) -> None:
            super().__init__()
            self._step = 0

        def get_input_audio_samplerate(self):
            return self.SR

        def get_audio_sample(self):
            # 前 3 块静音,5 块语音,之后持续静音(VAD 拖尾出句)
            self._step += 1
            n = int(self.SR * 0.1)  # 100ms/块
            if self._step <= 3 or self._step > 8:
                return np.zeros(n, dtype=np.float32)
            t = np.arange(n) / self.SR
            return (0.2 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)

    def __init__(self) -> None:
        self.media = self._ScriptedMedia()


class _FakePipeline:
    def __init__(self) -> None:
        self.calls: list[bytes] = []

    async def run_audio(self, pcm: bytes):
        self.calls.append(pcm)

        class R:
            error = None
            user_text = "假识别"
            reply_text = "假回复"
            audio_path = None

        return R()


def test_real_voice_loop_end_to_end():
    orch = MirrorOrchestrator(
        sim_mini=_FakeMini(), real_mini=_FakeRealMiniWithAudio(), run_mode="real_plus_sim"
    )
    bus = get_state_bus()
    bus.update("chat_mode", "voice")
    bus.update("real_conn_type", "wireless")  # 走 real_mini.media(脚本化假音源)
    pipe = _FakePipeline()
    loop = RealVoiceLoop(
        orch, pipe, idle_sleep_s=0.02,
        # 固定阈值 VAD:跳过自适应学习期(1.5s),保持脚本化音频的时序假设
        vad=EnergyVAD(rms_threshold=0.015),
    )
    loop.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not pipe.calls:
            time.sleep(0.05)
        assert pipe.calls, "5s 内 VAD 没出句 → pipeline 没被调"
        pcm = pipe.calls[0]
        assert isinstance(pcm, bytes) and len(pcm) > 3200, "PCM 太短不像一句话"
    finally:
        loop.stop()
