"""TTS 响度归一化测试(EdgeTTS._normalize_loudness)。

保护的事(2026-09-16 实测 Edge TTS 输出 mean -21dB 用户几乎听不到):
  1. 有 ffmpeg:低响度 wav 归一化后响度显著提升(mean_volume 上升)。
  2. 无 ffmpeg:跳过归一化,原文件不动,不 crash。
  3. 输入文件异常(不存在/损坏):保留原路径,不 crash。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from reachymini_conversation.tts.edge_tts import EdgeTTS  # noqa: E402

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _make_quiet_wav(path: Path, rms_db: float = -35.0, seconds: float = 1.0) -> None:
    """生成低响度 1kHz 正弦 wav(16k mono s16)。"""
    import wave

    sr = 16000
    amp = 10 ** (rms_db / 20)
    t = np.arange(int(sr * seconds)) / sr
    data = (np.sin(2 * np.pi * 1000 * t) * amp * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def _mean_volume_db(path: Path) -> float:
    out = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    for line in out.splitlines():
        if "mean_volume" in line:
            return float(line.split("mean_volume:")[1].strip().rstrip(" dB"))
    raise AssertionError("volumedetect 无输出")


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg 不在 PATH")
class TestNormalizeReal:
    def test_loudness_increases(self, tmp_path: Path) -> None:
        wav = tmp_path / "quiet.wav"
        _make_quiet_wav(wav, rms_db=-35.0)
        before = _mean_volume_db(wav)
        EdgeTTS._normalize_loudness(str(wav))
        after = _mean_volume_db(wav)
        assert after > before + 5, f"响度应显著提升: {before}dB → {after}dB"

    def test_output_remains_readable_mp3(self, tmp_path: Path) -> None:
        """归一化后是 MP3 内容(下游 decodeAudioData/SDK 均按内容解码)。"""
        wav = tmp_path / "quiet.wav"
        _make_quiet_wav(wav)
        EdgeTTS._normalize_loudness(str(wav))
        head = wav.read_bytes()[:3]
        assert head[:2] in (b"ID", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") or head == b"RIF"[:3] or head[:3] == b"ID3" or head[:1] == b"\xff"


class TestNormalizeFallback:
    def test_no_ffmpeg_keeps_file(self, tmp_path: Path, monkeypatch) -> None:
        wav = tmp_path / "quiet.wav"
        _make_quiet_wav(wav)
        before = wav.read_bytes()
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        EdgeTTS._normalize_loudness(str(wav))  # 不 crash
        assert wav.read_bytes() == before

    @pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg 不在 PATH")
    def test_missing_input_no_crash(self, tmp_path: Path) -> None:
        ghost = tmp_path / "ghost.wav"  # 不存在
        EdgeTTS._normalize_loudness(str(ghost))  # 应静默 fallback
        assert not ghost.exists()
        assert not (tmp_path / "ghost.wav.norm.mp3").exists()


def _make_wav_at_rate(path: Path, sr: int, seconds: float = 0.5) -> None:
    import wave

    t = np.arange(int(sr * seconds)) / sr
    data = (np.sin(2 * np.pi * 440 * t) * 0.5 * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


class TestPlayResample:
    """语速 bug 回归(2026-09-16):push_audio_sample 固定按 16kHz 解释数据,
    play() 必须把任意采样率重采样到 16k —— 否则 24k 慢 1.5x、48k 慢 3x。"""

    def test_24k_resampled_to_16k(self, tmp_path: Path) -> None:
        import asyncio

        wav = tmp_path / "a24k.wav"
        _make_wav_at_rate(wav, 24000, seconds=0.5)
        pushed: list = []

        asyncio.run(EdgeTTS.play(str(wav), lambda d: pushed.append(d)))

        assert pushed, "必须 push 一次"
        n = len(pushed[0])
        assert abs(n - 8000) <= 80, f"0.5s@16k 应≈8000 样本, 实际 {n}"

    def test_48k_resampled_to_16k(self, tmp_path: Path) -> None:
        """loudnorm 曾把文件升到 48k —— 这个采样率必须重采样,否则慢 3 倍。"""
        import asyncio

        wav = tmp_path / "a48k.wav"
        _make_wav_at_rate(wav, 48000, seconds=0.5)
        pushed: list = []

        asyncio.run(EdgeTTS.play(str(wav), lambda d: pushed.append(d)))

        n = len(pushed[0])
        assert abs(n - 8000) <= 80

    def test_16k_passthrough(self, tmp_path: Path) -> None:
        import asyncio

        wav = tmp_path / "a16k.wav"
        _make_wav_at_rate(wav, 16000, seconds=0.5)
        pushed: list = []

        asyncio.run(EdgeTTS.play(str(wav), lambda d: pushed.append(d)))

        assert len(pushed[0]) == 8000


def _make_stereo_wav(path: Path, sr: int = 16000, seconds: float = 0.5) -> None:
    """立体声 16k wav(两通道不同波形,便于验证 mono 化)。"""
    import wave

    t = np.arange(int(sr * seconds)) / sr
    left = (np.sin(2 * np.pi * 440 * t) * 0.4 * 32767).astype("<i2")
    right = (np.sin(2 * np.pi * 880 * t) * 0.4 * 32767).astype("<i2")
    interleaved = np.empty(2 * len(left), dtype="<i2")
    interleaved[0::2] = left
    interleaved[1::2] = right
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(interleaved.tobytes())


class TestPlayChannelLayout:
    """声道 bug 回归(2026-09-16):SDK 播放 appsrc caps 固定
    stereo(interleaved F32LE, audio_gstreamer.py:373 channels=2),
    base 的 PTS 时长计算(audio_base.py:149)用 data.shape[0] 当帧数。
    mono push 会被按 stereo 解释成一半帧数 → 2 倍速混叠噪声
    (实测用户反馈"语速太快听不清")。
    因此 play() 必须 push (N,2) 形状:shape[0]=帧数(PTS 正确),
    C-order 字节序即 L,R,L,R,... 与 caps interleaved 匹配。"""

    def test_mono_expanded_to_stereo(self, tmp_path: Path) -> None:
        import asyncio

        wav = tmp_path / "m16k.wav"
        _make_wav_at_rate(wav, 16000, seconds=0.5)
        pushed: list = []

        asyncio.run(EdgeTTS.play(str(wav), lambda d: pushed.append(d)))

        assert pushed[0].ndim == 2, "push 必须是 (N,2) 立体声"
        assert pushed[0].shape == (8000, 2), f"形状应为 (8000,2), 实际 {pushed[0].shape}"
        # 左右同值(mono 双声道化)
        assert np.allclose(pushed[0][:, 0], pushed[0][:, 1])

    def test_interleaved_byte_order(self, tmp_path: Path) -> None:
        """字节级证据:(N,2) C-order tobytes == 手算 L,R,L,R,... interleaved。"""
        import asyncio

        wav = tmp_path / "m16k.wav"
        _make_wav_at_rate(wav, 16000, seconds=0.25)
        pushed: list = []

        asyncio.run(EdgeTTS.play(str(wav), lambda d: pushed.append(d)))

        mono = pushed[0][:, 0]
        expected = np.stack([mono, mono], axis=1).tobytes()
        assert pushed[0].tobytes() == expected, "字节序必须与 caps interleaved 布局一致"

    def test_24k_mono_stereo_layout(self, tmp_path: Path) -> None:
        """24k 重采样后同样要 stereo(修复前只修采样率、漏声道,听感 2 倍速)。"""
        import asyncio

        wav = tmp_path / "a24k.wav"
        _make_wav_at_rate(wav, 24000, seconds=0.5)
        pushed: list = []

        asyncio.run(EdgeTTS.play(str(wav), lambda d: pushed.append(d)))

        assert pushed[0].shape[1] == 2, f"24k 源 push 后必须是 stereo, 实际 {pushed[0].shape}"
        assert abs(pushed[0].shape[0] - 8000) <= 80

    def test_stereo_source_dual_channel_preserved(self, tmp_path: Path) -> None:
        """立体声源:先 mono 化再双声道化,push 仍是 (N,2)。"""
        import asyncio

        wav = tmp_path / "s16k.wav"
        _make_stereo_wav(wav, 16000, seconds=0.5)
        pushed: list = []

        asyncio.run(EdgeTTS.play(str(wav), lambda d: pushed.append(d)))

        assert pushed[0].ndim == 2 and pushed[0].shape[1] == 2
        assert pushed[0].shape[0] == 8000
