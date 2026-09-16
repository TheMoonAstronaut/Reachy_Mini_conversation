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

# 自适应底噪(2026-09-16 实测:ReSpeaker 当前环境底噪 rms=0.024 > 固定阈值
# 0.015 → VAD 永远"说话中" → 12s 超长截断循环 → ASR 空结果;板载声卡底噪
# 0.04 同样击穿)。不传 rms_threshold 时启用:阈值 = 底噪EMA × 2.5,钳 [0.008, 0.1]。
NOISE_RATIO = 2.5
MIN_THRESHOLD = 0.008
MAX_THRESHOLD = 0.10
NOISE_EMA_ALPHA = 0.1  # 每 chunk 更新速率(~0.5s/chunk,约 5s 收敛到 63%)


class EnergyVAD:
    """能量 VAD 分句器(逐 chunk 喂,语句完整时返回)。

    阈值两种模式:
      - 显式传 rms_threshold → 固定阈值(测试/向后兼容)
      - 默认 None → 自适应底噪:静音期维护底噪 EMA,阈值 = EMA×2.5
        钳制在 [0.008, 0.10]。安静房间更灵敏,吵闹环境不死循环。
    """

    def __init__(
        self,
        rms_threshold: float | None = None,
        min_speech_ms: float = 250.0,
        hangover_ms: float = 700.0,
        pre_speech_ms: float = 200.0,
        max_speech_s: float = 12.0,
        *,
        noise_ratio: float = NOISE_RATIO,
        min_threshold: float = MIN_THRESHOLD,
        max_threshold: float = MAX_THRESHOLD,
        noise_ema_alpha: float = NOISE_EMA_ALPHA,
        learn_s: float = 1.5,
    ) -> None:
        self.rms_threshold = rms_threshold  # None → 自适应
        self.min_speech_ms = min_speech_ms
        self.hangover_ms = hangover_ms
        self.pre_speech_ms = pre_speech_ms
        self.max_speech_s = max_speech_s
        self.noise_ratio = noise_ratio
        self.min_threshold = min_threshold
        self.max_threshold = max_threshold
        self.noise_ema_alpha = noise_ema_alpha
        # 冷启动学习期:前 learn_s 秒只学底噪不触发(防"EMA 初值低 → 底噪
        # 被判语音 → 语句态不更新 EMA → 永远学不到"死锁,2026-09-16 实测)。
        # 一次性:语句完成后的 reset() 不重置(环境底噪不因切句变化)。
        self.learn_s = learn_s
        self._learned = False
        self._learn_elapsed = 0.0
        # 底噪 EMA 初值:安静房间假设;reset() 不清(环境不因切句而改变)
        self._noise_ema = DEFAULT_RMS_THRESHOLD / 2.0

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
        threshold = self._threshold()
        is_loud = rms >= threshold

        if not self._in_speech:
            if self.rms_threshold is None:
                # 自适应:SILENCE 态所有 chunk 都学底噪(含 loud——loud 的
                # 底噪正是要学的;语句开始后才停学,防语音污染估计)
                a = self.noise_ema_alpha
                self._noise_ema = (1.0 - a) * self._noise_ema + a * rms
                if not self._learned:
                    self._learn_elapsed += chunk_ms / 1000.0
                    if self._learn_elapsed >= self.learn_s:
                        self._learned = True
                        logger.info(
                            f"[VAD] 底噪学习完成: EMA={self._noise_ema:.4f},"
                            f" 阈值={self._threshold():.4f}"
                        )
                    # 学习期不触发语句;前导缓冲照常维护
                    self._pre_buffer.append(samples)
                    self._pre_buffer_ms += chunk_ms
                    while self._pre_buffer_ms > self.pre_speech_ms and len(self._pre_buffer) > 1:
                        self._pre_buffer_ms -= len(self._pre_buffer.popleft()) / sample_rate * 1000.0
                    return None
            if is_loud:
                self._in_speech = True
                self._speech_chunks = list(self._pre_buffer) + [samples]
                self._speech_ms = self._pre_buffer_ms + chunk_ms
                self._silence_ms = 0.0
                self._pre_buffer.clear()
                self._pre_buffer_ms = 0.0
                logger.debug(f"[VAD] 语句开始(rms={rms:.4f}, 阈值={threshold:.4f})")
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
                f"(rms 峰值 {float(np.sqrt(np.max(seg**2))):.3f}, 阈值 {threshold:.4f})"
            )
            self.reset()
            return seg
        return None

    def _threshold(self) -> float:
        """当前生效阈值:显式传 rms_threshold 用固定值;否则按底噪 EMA 自适应。"""
        if self.rms_threshold is not None:
            return self.rms_threshold
        return min(
            max(self._noise_ema * self.noise_ratio, self.min_threshold),
            self.max_threshold,
        )

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
