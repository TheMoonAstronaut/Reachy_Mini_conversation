"""reachymini_conversation.utils.audio_convert — Gradio 录音 → ASR PCM 转换(B1)。

Gradio `gr.Audio(type="numpy")` 给的是 `(sample_rate, np.ndarray)`:
  - dtype 可能是 int16 / int32 / float32 / float64(浏览器录音经 ffmpeg 解码后多为 int16,
    但不同浏览器/格式可能给 float,这里全部兼容)
  - shape 是 (samples,) 单声道 或 (samples, channels) 多声道

豆包 ASR 要求:PCM 16-bit / 16 kHz / mono 的 little-endian bytes。

用法:
    from reachymini_conversation.utils.audio_convert import numpy_to_pcm16_bytes
    pcm = numpy_to_pcm16_bytes(sample_rate, data)  # -> bytes(16kHz mono int16)
"""

from __future__ import annotations

from math import gcd

import numpy as np
from scipy import signal

# 豆包 ASR 协议要求的采样率(openspeech.bytedance.com bigmodel_nostream)
ASR_TARGET_SAMPLE_RATE = 16000


def numpy_to_pcm16_bytes(
    sample_rate: int,
    data: np.ndarray,
    target_rate: int = ASR_TARGET_SAMPLE_RATE,
) -> bytes:
    """把 Gradio 的 (sample_rate, ndarray) 转成 PCM 16-bit/target_rate/mono bytes。

    Args:
        sample_rate: 录音原始采样率(Hz)
        data: numpy 音频数据,int16/int32/uint8/float 均可,(N,) 或 (N, C)
        target_rate: 目标采样率,默认 16000(豆包 ASR 要求)

    Returns:
        little-endian int16 PCM bytes;空输入返回 b""

    Raises:
        ValueError: sample_rate 非法(<=0)
    """
    if sample_rate is None or int(sample_rate) <= 0:
        raise ValueError(f"非法采样率: {sample_rate!r}")

    arr = np.asarray(data)
    if arr.size == 0:
        return b""

    # 1. 多声道 → 单声道(Gradio 约定 shape=(samples, channels))
    if arr.ndim == 2:
        arr = arr.mean(axis=1)
    arr = arr.reshape(-1)

    # 2. dtype 统一成 float32 [-1.0, 1.0]
    if arr.dtype == np.int16:
        samples = arr.astype(np.float32) / 32768.0
    elif arr.dtype == np.int32:
        samples = arr.astype(np.float32) / 2147483648.0
    elif arr.dtype == np.uint8:
        samples = (arr.astype(np.float32) - 128.0) / 128.0
    else:
        # float32/float64:浏览器解码惯例已是 [-1, 1] 归一化
        samples = arr.astype(np.float32)

    # 3. 清洗 + 限幅(防爆音/NaN)
    samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
    samples = np.clip(samples, -1.0, 1.0)

    # 4. 重采样到 target_rate(polyphase,gcd 约分保证整数比)
    src_rate = int(sample_rate)
    if src_rate != target_rate:
        g = gcd(src_rate, target_rate)
        up, down = target_rate // g, src_rate // g
        samples = signal.resample_poly(samples, up, down).astype(np.float32)

    # 5. float [-1,1] → int16 little-endian bytes
    pcm = np.round(samples * 32767.0).astype("<i2")
    return pcm.tobytes()
