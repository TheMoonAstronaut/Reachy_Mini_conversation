"""reachymini_conversation.voice_loop — 免提连续语音的能量 VAD 分句器(V2.3)。

职责(SPEC_V2.md D4):
  浏览器流式麦克风 chunk 持续喂入 → 能量 VAD 检出"说一句"的边界 →
  输出完整语句(16kHz float32)给 pipeline.run_audio()。

设计决策:
  - 能量 VAD(决策 15:沿用简单阈值,后续可升 WebRTC VAD / silero)
  - 原始采样率累积,语句结束时一次性转 16k(避免逐 chunk 重采样的边界伪影)
  - 前导保护:语句起点往前多留 pre_speech_ms(防吃掉词头)
  - 无打断(barge-in 本期不做):外部在 TTS 播放中不要 push,或调用 reset()

用法:
    vad = EnergyVAD()
    seg = vad.push(sample_rate, chunk_f32)   # 每个浏览器 chunk 调一次
    if seg is not None:
        pcm = numpy_to_pcm16_bytes(16000, seg)
        await pipeline.run_audio(pcm)
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# 默认阈值(float32 [-1,1] 的 RMS):安静房间底噪 ~0.001-0.01,正常说话 0.02+
DEFAULT_RMS_THRESHOLD = 0.015


class EnergyVAD:
    """能量 VAD 分句器(逐 chunk 喂,语句完整时返回)。"""

    def __init__(
        self,
        rms_threshold: float = DEFAULT_RMS_THRESHOLD,
        min_speech_ms: float = 250.0,
        hangover_ms: float = 700.0,
        pre_speech_ms: float = 200.0,
        max_speech_s: float = 12.0,
    ) -> None:
        self.rms_threshold = rms_threshold
        self.min_speech_ms = min_speech_ms
        self.hangover_ms = hangover_ms
        self.pre_speech_ms = pre_speech_ms
        self.max_speech_s = max_speech_s

        self._in_speech = False
        self._speech_chunks: list[np.ndarray] = []
        self._silence_ms = 0.0  # 语句内连续静音累计
        self._speech_ms = 0.0
        # 前导环形缓冲(SILENCE 态保留最近 pre_speech_ms 的音频)
        self._pre_buffer: deque[np.ndarray] = deque(maxlen=8)
        self._pre_buffer_ms = 0.0

    def reset(self) -> None:
        """清空状态(切模式 / 打断时调)。"""
        self._in_speech = False
        self._speech_chunks.clear()
        self._pre_buffer.clear()
        self._silence_ms = 0.0
        self._speech_ms = 0.0
        self._pre_buffer_ms = 0.0

    def push(self, sample_rate: int, data: Any) -> np.ndarray | None:
        """喂一块音频,返回完整语句(原始采样率 float32)或 None。

        Args:
            sample_rate: 该 chunk 的采样率(浏览器常为 48000/44100)
            data: numpy 数组,int/float 均可,(N,) 或 (N, C)
        """
        samples = self._to_f32_mono(data)
        if samples.size == 0:
            return None
        chunk_ms = len(samples) / sample_rate * 1000.0
        rms = float(np.sqrt(np.mean(samples**2)))
        is_loud = rms >= self.rms_threshold

        if not self._in_speech:
            if is_loud:
                self._in_speech = True
                self._speech_chunks = list(self._pre_buffer) + [samples]
                self._speech_ms = self._pre_buffer_ms + chunk_ms
                self._silence_ms = 0.0
                self._pre_buffer.clear()
                self._pre_buffer_ms = 0.0
                logger.debug(f"[VAD] 语句开始(rms={rms:.4f})")
            else:
                # 静音期:维护前导缓冲
                self._pre_buffer.append(samples)
                self._pre_buffer_ms += chunk_ms
                while self._pre_buffer_ms > self.pre_speech_ms and len(self._pre_buffer) > 1:
                    self._pre_buffer_ms -= len(self._pre_buffer.popleft()) / sample_rate * 1000.0
            return None

        # 语句中
        self._speech_chunks.append(samples)
        self._speech_ms += chunk_ms
        if is_loud:
            self._silence_ms = 0.0
        else:
            self._silence_ms += chunk_ms

        # 结束判定:静音拖尾足够长 / 语句超长
        if self._silence_ms >= self.hangover_ms or self._speech_ms >= self.max_speech_s * 1000.0:
            if self._speech_ms - self._silence_ms < self.min_speech_ms:
                # 太短(咳嗽/点击声)→ 丢弃
                logger.debug(f"[VAD] 丢弃过短片段({self._speech_ms:.0f}ms)")
                self.reset()
                return None
            seg = np.concatenate(self._speech_chunks)
            logger.info(
                f"[VAD] 语句完成: {len(seg)/sample_rate:.1f}s "
                f"(rms 峰值 {float(np.sqrt(np.max(seg**2))):.3f})"
            )
            self.reset()
            return seg
        return None

    @staticmethod
    def _to_f32_mono(data: Any) -> np.ndarray:
        """任意 dtype/声道 → float32 [-1,1] mono(与 audio_convert 同规则)。"""
        arr = np.asarray(data)
        if arr.size == 0:
            return np.zeros(0, dtype=np.float32)
        if arr.ndim == 2:
            arr = arr.mean(axis=1)
        arr = arr.reshape(-1)
        if arr.dtype == np.int16:
            out = arr.astype(np.float32) / 32768.0
        elif arr.dtype == np.int32:
            out = arr.astype(np.float32) / 2147483648.0
        elif arr.dtype == np.uint8:
            out = (arr.astype(np.float32) - 128.0) / 128.0
        else:
            out = arr.astype(np.float32)
        return np.nan_to_num(np.clip(out, -1.0, 1.0), nan=0.0, posinf=1.0, neginf=-1.0)
