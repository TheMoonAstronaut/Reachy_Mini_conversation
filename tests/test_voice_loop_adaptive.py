"""EnergyVAD 自适应底噪测试(2026-09-16 高底噪环境实测驱动)。

事故背景:ReSpeaker 当前环境底噪 rms=0.024 > 固定阈值 0.015,
VAD 永远"说话中" → 12s 超长截断循环 → 底噪段送 ASR → 全部空结果,
用户体感"有时能听到有时听不到"。板载声卡底噪 rms=0.04 同病。

保护的事:
  1. 高底噪(0.024)环境:底噪收敛后不触发;语音(0.15)正常检出。
  2. 安静房间(0.001)保持灵敏(0.02 语音可检出)—— 不牺牲原场景。
  3. 阈值钳制:底噪爆表不超 0.10;全零底噪不低于 0.008。
  4. 显式 rms_threshold → 固定阈值,行为与旧版一致(向后兼容)。
  5. reset() 不清底噪 EMA(环境底噪不因切句变化)。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from reachymini_conversation.voice_loop import EnergyVAD  # noqa: E402

_SR = 16000
_CHUNK = 8000  # 0.5s


def _noise(rms: float, n: int = _CHUNK, seed: int = 42) -> np.ndarray:
    """恒定能量的随机噪声(模拟环境底噪)。"""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n).astype(np.float32)
    return (x / np.sqrt(np.mean(x**2)) * rms).astype(np.float32)


def _tone(rms: float, n: int = _CHUNK, freq: float = 220.0) -> np.ndarray:
    """正弦波(模拟语音,频谱与噪声不同,幅值稳定)。"""
    t = np.arange(n) / _SR
    return (np.sin(2 * np.pi * freq * t) * rms * np.sqrt(2)).astype(np.float32)


def _feed(vad: EnergyVAD, seg: np.ndarray) -> np.ndarray | None:
    """按 0.5s chunk 喂一段音频,返回最后一次非 None 输出。"""
    out = None
    for i in range(0, len(seg), _CHUNK):
        r = vad.push(_SR, seg[i : i + _CHUNK])
        if r is not None:
            out = r
    return out


class TestAdaptiveNoiseFloor:
    def test_high_noise_floor_no_false_trigger(self) -> None:
        """核心回归:底噪 0.024(实测值)持续 6s → EMA 收敛后不再误触发。"""
        vad = EnergyVAD()  # 默认自适应
        # 前 1s 可能还偶尔触发(EMA 未收敛),喂够 6s 后必须稳定不触发
        assert _feed(vad, _noise(0.024, _SR * 6)) is None

    def test_speech_detected_over_high_noise(self) -> None:
        """高底噪中说话(rms 0.15)仍检出,且语句含语音内容。"""
        vad = EnergyVAD()
        _feed(vad, _noise(0.024, _SR * 4))  # 先收敛底噪
        speech = np.concatenate(
            [_tone(0.15, _SR), _noise(0.024, int(_SR * 1.2))]  # 1s 语音 + 拖尾静音
        )
        seg = _feed(vad, speech)
        assert seg is not None, "高底噪中的语音必须检出"
        peak = float(np.sqrt(np.max(seg**2)))
        assert peak > 0.1

    def test_quiet_room_stays_sensitive(self) -> None:
        """安静房间(底噪 0.001)灵敏度不回归:0.02 语音可检出。"""
        vad = EnergyVAD()
        _feed(vad, _noise(0.001, _SR * 3))
        speech = np.concatenate([_tone(0.02, _SR), _noise(0.001, int(_SR * 1.2))])
        assert _feed(vad, speech) is not None


class TestThresholdBounds:
    def test_threshold_capped(self) -> None:
        vad = EnergyVAD()
        vad._noise_ema = 0.5  # 底噪爆表
        assert vad._threshold() == vad.max_threshold

    def test_threshold_floored(self) -> None:
        vad = EnergyVAD()
        vad._noise_ema = 0.0  # 全零底噪
        assert vad._threshold() == vad.min_threshold

    def test_fixed_threshold_compat(self) -> None:
        """显式传阈值 → 永远固定(现有单测/真机调参场景不变)。"""
        vad = EnergyVAD(rms_threshold=0.015)
        _feed(vad, _noise(0.005, _SR * 3))  # 喂底噪也不应改变阈值
        assert vad._threshold() == 0.015

    def test_reset_keeps_noise_ema(self) -> None:
        vad = EnergyVAD()
        _feed(vad, _noise(0.03, _SR * 4))
        ema_before = vad._noise_ema
        vad.reset()
        assert vad._noise_ema == ema_before
