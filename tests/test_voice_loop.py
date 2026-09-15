"""EnergyVAD 分句器的单元测试(V2.3)。

合成音频驱动:静音段 + 语音段(正弦)+ 静音拖尾,断言:
  1. 完整语句被检出且只输出一次
  2. 静音永不输出
  3. 过短爆发(咳嗽)被丢弃
  4. 超长语句被 max_speech_s 截断输出
  5. 前导缓冲:语句起点往前多留 pre_speech_ms(不吃词头)
  6. reset() 中断进行中的语句
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from reachymini_conversation.voice_loop import EnergyVAD  # noqa: E402

SR = 48000  # 浏览器常见采样率


def _silence(ms: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(sr * ms / 1000), dtype=np.float32)


def _tone(ms: float, sr: int = SR, amp: float = 0.2) -> np.ndarray:
    """模拟语音的高 RMS 信号(200Hz 正弦)。"""
    t = np.arange(int(sr * ms / 1000)) / sr
    return (amp * np.sin(2 * np.pi * 200 * t)).astype(np.float32)


def test_sentence_detected_once():
    vad = EnergyVAD(rms_threshold=0.015, hangover_ms=500)
    out = []
    for chunk in [_silence(300), _tone(800), _silence(300), _silence(300)]:
        r = vad.push(SR, chunk)
        if r is not None:
            out.append(r)
    assert len(out) == 1, "一句语音应恰好输出一次"
    # 语句长度 ≈ 前导 200ms + 语音 800ms + 拖尾 500ms
    dur_s = len(out[0]) / SR
    assert 1.0 < dur_s < 2.2, f"语句长度异常: {dur_s:.2f}s"


def test_silence_never_emits():
    vad = EnergyVAD()
    for _ in range(20):
        assert vad.push(SR, _silence(200)) is None


def test_short_burst_discarded():
    """200ms 的短促声音(< min_speech)不应触发输出。"""
    vad = EnergyVAD(rms_threshold=0.015, min_speech_ms=300, hangover_ms=400)
    out = []
    for chunk in [_tone(150), _silence(600)]:
        r = vad.push(SR, chunk)
        if r is not None:
            out.append(r)
    assert not out, "过短爆发应被丢弃"


def test_max_speech_truncates():
    vad = EnergyVAD(rms_threshold=0.015, max_speech_s=2.0)
    out = []
    for _ in range(15):  # 连续 15×400ms = 6s 语音
        r = vad.push(SR, _tone(400))
        if r is not None:
            out.append(r)
    assert len(out) >= 1, "超长语句应被截断输出"
    assert len(out[0]) / SR <= 2.6


def test_pre_speech_kept():
    """语句起点要往前多留 pre_speech_ms(词头不被切掉)。"""
    vad = EnergyVAD(rms_threshold=0.015, pre_speech_ms=300, hangover_ms=400)
    vad.push(SR, _silence(200))
    vad.push(SR, _silence(200))  # 前导缓冲累计 400ms,应保留最近 300ms
    vad.push(SR, _tone(500))
    seg = None
    r = vad.push(SR, _silence(600))
    if r is not None:
        seg = r
    assert seg is not None
    dur_ms = len(seg) / SR * 1000
    # 500ms 语音 + ~300ms 前导 + 拖尾(400ms hangover 或 600ms 静音块全部)
    assert dur_ms >= 500 + 250, f"前导缓冲丢了?dur={dur_ms:.0f}ms"


def test_reset_discards_pending():
    vad = EnergyVAD(rms_threshold=0.015)
    vad.push(SR, _tone(600))  # 语句进行中
    vad.reset()
    # reset 后静音不应触发任何输出
    assert vad.push(SR, _silence(900)) is None


def test_int16_and_stereo_input():
    """int16 + 双声道输入也要能正常分句(浏览器 chunk 格式兼容)。"""
    vad = EnergyVAD(rms_threshold=0.015, hangover_ms=400)
    tone = (_tone(600) * 32767).astype(np.int16)
    stereo = np.stack([tone, tone], axis=1)  # (N, 2)
    assert vad.push(SR, _silence(200).astype(np.int16)) is None
    assert vad.push(SR, stereo) is None
    seg = vad.push(SR, _silence(600).astype(np.int16))
    assert seg is not None and seg.dtype == np.float32
