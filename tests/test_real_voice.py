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
from reachymini_conversation.state_bus import get_state_bus, reset_state_bus  # noqa: E402
from reachymini_conversation.voice_loop import EnergyVAD  # noqa: E402


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
    # 线程泄漏检测:后台 loop 线程(或意外创建的 Gst GLib 线程)残留
    # 会在后续用例与 Gst audiomixer 线程竞态段错误(2026-09-16 实案:
    # test_router_real_push_failure_degrades 未 mock 创建真声卡单例 →
    # e2e 用例崩溃于 libgstaudio)。残留即打印,提早暴露。
    import sys as _sys
    import threading as _threading

    alive = [
        t.name
        for t in _threading.enumerate()
        if t is not _threading.current_thread() and t.name != "MainThread"
    ]
    if alive:
        print(f"\n[warn] 用例结束后残留线程: {alive}", file=_sys.stderr)


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


def test_router_wired_wires_wobbler_on_first_play(monkeypatch):
    """wired 首次播放必须接通 wobbler(说话特别动作,2026-09-16 接线)。

    回归:接线前真机说话时头部完全不动(offsets 无处送达)。
    验证:enable_wobbling 被调且回调发送 SetSpeechOffsetsCmd 到 real daemon;
    同时 daemon 收到 SetWobblingCmd(enabled=True)。
    """
    from reachy_mini.io.protocol import SetSpeechOffsetsCmd, SetWobblingCmd

    sim, real = _FakeMini(), _FakeMini()
    orch = MirrorOrchestrator(sim_mini=sim, real_mini=real, run_mode="real_plus_sim")
    get_state_bus().update("real_conn_type", "wired")

    sent: list = []

    class _FakeClient:
        def send_command(self, cmd):
            sent.append(cmd)

    real.client = _FakeClient()

    class _FakeLocalAudio:
        def __init__(self):
            self.started = False
            self.wobble_cb = None
            self.pushed = []

        def start_playing(self):
            self.started = True

        def enable_wobbling(self, cb):
            self.wobble_cb = cb

        def push_audio_sample(self, pcm):
            self.pushed.append(pcm)

    fake_audio = _FakeLocalAudio()
    import reachymini_conversation.local_audio as la

    monkeypatch.setattr(la, "get_local_audio", lambda: fake_audio)
    route = make_tts_audio_router(lambda: orch)
    route(np.zeros(1600, dtype=np.float32))  # 首次播放 → 接线

    assert fake_audio.started and len(fake_audio.pushed) == 1
    assert fake_audio.wobble_cb is not None, "wired 首次播放必须 enable_wobbling"

    # 回调 → offsets 命令送达 daemon B
    fake_audio.wobble_cb((0.001, 0.0, 0.0, 0.01, 0.0, 0.0))
    assert any(isinstance(c, SetSpeechOffsetsCmd) for c in sent), (
        "wobbler 回调必须发送 SetSpeechOffsetsCmd(否则说话时头部不动)"
    )
    assert any(
        isinstance(c, SetWobblingCmd) and c.enabled for c in sent
    ), "daemon 端必须收到 SetWobblingCmd(enabled=True)"


def test_router_no_orch_no_crash():
    route = make_tts_audio_router(lambda: None)
    route(np.zeros(10, dtype=np.float32))  # 不崩即可


def test_router_real_push_failure_degrades(monkeypatch):
    """real_plus_sim(wired)推送抛异常 → route 吞掉不炸(但绝不创建真声卡)。

    历史坑(2026-09-16 段错误排查):本测试曾未 mock get_local_audio,
    wired 默认分支真创建了 GStreamerAudio 单例(GLib 线程 + 真 pipeline),
    后续用例的后台线程与 Gst audiomixer 线程竞态 → libgstaudio 段错误。
    """
    class _BadMedia:
        def push_audio_sample(self, pcm):
            raise RuntimeError("audio device gone")

    class _BadMini:
        media = _BadMedia()

    orch = MirrorOrchestrator(sim_mini=_FakeMini(), real_mini=_BadMini(), run_mode="real_plus_sim")
    get_state_bus().update("real_conn_type", "wired")
    # 必须 mock:真创建 GStreamerAudio 会在 pytest 进程里留 Gst 管线线程,
    # 与后续用例线程竞态段错误(见 docstring)
    import reachymini_conversation.local_audio as la

    bad_audio = _BadMedia()
    bad_audio.start_playing = lambda: None
    monkeypatch.setattr(la, "get_local_audio", lambda: bad_audio)
    route = make_tts_audio_router(lambda: orch)
    route(np.zeros(10, dtype=np.float32))  # push 异常被吞,不炸轮次


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


# ---------- 播放后冷却:防回声连播(2026-09-16 日志实锤)----------


class _FakePipelineWithAudio:
    """返回 audio_path 的 pipeline(0.1s 短 wav),模拟"本轮要播放 TTS"。"""
    SR = 16000

    def __init__(self, audio_path: str) -> None:
        self.calls: list[bytes] = []
        self._audio_path = audio_path

    async def run_audio(self, pcm: bytes):
        self.calls.append(pcm)

        class R:
            error = None
            user_text = "假识别"
            reply_text = "假回复"
            audio_path = self._audio_path

        return R()


class _OneUtteranceMedia(_FakeMedia):
    """假音源:仅一句"语音"(3 静音 + 5 语音块),之后持续静音。

    块=100ms,amp 0.2 sine;空转供给(不模拟真实时间节奏,时序断言
    见单测,端到端时序由真机实测覆盖)。
    """

    def __init__(self) -> None:
        super().__init__()
        self._step = 0
        self._n = int(16000 * 0.1)

    def get_input_audio_samplerate(self):
        return 16000

    def get_audio_sample(self):
        self._step += 1
        s = self._step
        if 4 <= s <= 8:
            t = np.arange(self._n) / 16000
            return (0.2 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
        return np.zeros(self._n, dtype=np.float32)


def _make_short_wav(path) -> str:
    import wave

    n = int(16000 * 0.1)
    t = np.arange(n) / 16000
    data = (0.1 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(data.tobytes())
    return str(path)


def _mk_echo_loop(tmp_path, cooldown_s: float):
    """构造:脚本化音源(仅一句)+ 返回 audio_path 的 pipeline + 注入冷却参数。"""
    wav = _make_short_wav(tmp_path / "tts.wav")
    orch = MirrorOrchestrator(
        sim_mini=_FakeMini(), real_mini=_FakeMini(), run_mode="real_plus_sim"
    )
    bus = get_state_bus()
    bus.update("chat_mode", "voice")
    bus.update("real_conn_type", "wireless")
    media = _OneUtteranceMedia()
    orch.real_mini.media = media
    pipe = _FakePipelineWithAudio(wav)
    loop = RealVoiceLoop(
        orch, pipe, idle_sleep_s=0.01,
        vad=EnergyVAD(rms_threshold=0.015),
        playback_margin_s=0.05,
        playback_cooldown_s=cooldown_s,
    )
    return loop, pipe


def test_playback_sets_cooldown_and_mute(tmp_path):
    """TTS 轮次后必须设置静音期 + 冷却期(2026-09-16 回声连播修复)。

    日志证据:11:45:58 TTS → 11:46:04 回声"语句" → 11:46:12 又一条 TTS
    (连播,用户听感"语速太快")。播放后冷却期内 VAD 出的句应被丢弃。
    """
    loop, pipe = _mk_echo_loop(tmp_path, cooldown_s=0.3)
    loop.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not pipe.calls:
            time.sleep(0.05)
        assert pipe.calls, "句1 应正常通过(无冷却)"
        now = time.monotonic()
        assert loop._mute_until > now, "播放后轮次必须设置麦克风静音期"
        # 冷却 = 播放时长(0.1s) + cooldown(0.3s),轮次刚结束不久,应仍有效
        assert loop._cooldown_until > now + 0.2, (
            f"冷却期未生效: cooldown_until={loop._cooldown_until:.2f} now={now:.2f}"
        )
        assert loop._cooldown_until >= loop._mute_until, "冷却应覆盖静音余量"
    finally:
        loop.stop()


def test_cooldown_drops_echo_turn(tmp_path):
    """冷却期内的 VAD 语句必须被丢弃,不进 ASR/pipeline(回声连播回归)。"""
    loop, pipe = _mk_echo_loop(tmp_path, cooldown_s=0.3)
    # 预置:未来 30s 都在冷却期内(模拟"刚播完 TTS 的回声尾巴")
    loop._cooldown_until = time.monotonic() + 30.0
    loop.start()
    try:
        time.sleep(2.0)  # 足够 VAD 出句并被冷却丢弃
        assert not pipe.calls, (
            f"冷却期内 VAD 出的句必须被丢弃,实际 pipeline 被调 {len(pipe.calls)} 次"
        )
    finally:
        loop.stop()
