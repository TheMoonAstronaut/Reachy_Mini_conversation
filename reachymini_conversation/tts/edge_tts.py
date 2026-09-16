"""reachymini_conversation.tts.edge_tts — Microsoft Edge TTS(P4)。

沿用根目录 `tts.py` 实现,P4 改:
  - 用 config_helper 读 voice 配置(可热重载)
  - 提供 async 接口(原版只有 subprocess 同步)
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile

logger = logging.getLogger(__name__)


class EdgeTTS:
    """Microsoft Edge TTS 包装。

    用法:
        tts = EdgeTTS()
        wav_path = tts.synthesize("你好")
        await tts.play(wav_path, push_audio_sample_fn)
    """

    def __init__(self, voice: str | None = None) -> None:
        from reachymini_conversation.config_helper import get_tts_config

        cfg = get_tts_config()
        self.voice = voice or cfg.get("voice", "zh-CN-XiaoxiaoNeural")

    def synthesize(self, text: str, output_file: str | None = None) -> str | None:
        """同步合成:返回 wav 文件路径,失败返回 None。

        用 edge-tts CLI(subprocess)。
        """
        if not text or not text.strip():
            return None

        if output_file is None:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.close()
            output_file = tmp.name

        cmd = [
            "edge-tts",
            "--voice",
            self.voice,
            "--text",
            text,
            "--write-media",
            output_file,
        ]

        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=30.0,
            )
            self._normalize_loudness(output_file)
            logger.info(f"[TTS] Generated ({self.voice}): {output_file}")
            return output_file
        except subprocess.CalledProcessError as e:
            logger.error(f"[TTS] edge-tts failed: {e.stderr[:200]}")
            return None
        except subprocess.TimeoutExpired:
            logger.error(f"[TTS] edge-tts timeout (>30s) for text: {text[:50]}")
            return None
        except FileNotFoundError:
            # edge-tts CLI 没装
            logger.error("[TTS] edge-tts CLI not found; install via `pip install edge-tts`")
            return None

    @staticmethod
    def _normalize_loudness(path: str) -> None:
        """ffmpeg loudnorm 响度归一化(I=-14 LUFS / TP=-1.5dB);失败保留原文件。

        背景(2026-09-16 实测):Edge TTS 原始输出 mean_volume≈-21dB、
        max≈-5.4dB,真机扬声器(alsa 已 0dB)和浏览器播放都明显偏小,
        用户反馈"几乎听不到"。loudnorm 单程模式对语音播报足够。

        注意:edge-tts 输出实为 MP3(虽以 .wav 命名),ffmpeg 按内容探测
        输入格式;输出临时文件显式 libmp3lame 编码,再原子替换回原路径
        —— 内容格式与现状一致(下游 decodeAudioData / SDK play 均兼容)。
        """
        import os
        import shutil

        if shutil.which("ffmpeg") is None:
            logger.warning("[TTS] 无 ffmpeg,跳过响度归一化(音量可能偏小)")
            return
        tmp_out = path + ".norm.mp3"
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", path,
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
            # loudnorm 会把采样率升到 48k/192k;edge-tts 原生 24kHz。
            # 采样率漂移会让播放端变速(真机管线固定 16k 解释,见 play()),
            # 必须钉回 24k。
            "-ar", "24000",
            "-c:a", "libmp3lame",
            "-f", "mp3",
            tmp_out,
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30.0)
            os.replace(tmp_out, path)
        except Exception as e:
            logger.warning(f"[TTS] 响度归一化失败(保留原音量): {e}")
            if os.path.exists(tmp_out):
                os.unlink(tmp_out)

    async def synthesize_async(self, text: str) -> str | None:
        """async 包装:在线程池跑同步 synthesize。"""
        return await asyncio.to_thread(self.synthesize, text)

    @staticmethod
    async def play(
        wav_path: str,
        push_audio_sample_fn,
    ) -> None:
        """读 wav → 转 PCM → push 到机器人(sim 模拟器 / 真机)。

        Args:
            wav_path: synthesize 返回的 wav 路径
            push_audio_sample_fn: 形如 `reachy.media.push_audio_sample(pcm_array)` 的 callable

        关键约束(2026-09-16 调研 SDK audio_base.py:116):ReSpeaker 播放管线
        固定按 16000Hz 解释推送的数据,push_audio_sample 不携带采样率。
        因此必须把任意采样率的文件重采样到 16k 再 push —— 否则 24k 文件
        慢 1.5 倍、48k 文件慢 3 倍(实测用户反馈"语速降低")。
        """
        try:
            import numpy as np
            import soundfile as sf

            data, samplerate = await asyncio.to_thread(sf.read, wav_path, dtype="float32")
            # 转 mono(机器人需要单声道)
            if data.ndim > 1:
                data = data.mean(axis=1)
            # 采样率归一到 16k(播放管线固定 16k 解释)
            if samplerate != 16000:
                from math import gcd

                from scipy import signal

                g = gcd(int(samplerate), 16000)
                data = signal.resample_poly(
                    data, 16000 // g, int(samplerate) // g
                ).astype(np.float32)
            # 限幅(避免 clipping)
            data = np.clip(data, -1.0, 1.0).astype(np.float32)
            await asyncio.to_thread(push_audio_sample_fn, data)
            logger.debug(f"[TTS] Played {len(data)} samples @ 16000Hz(源 {samplerate}Hz)")
        except Exception as e:
            logger.warning(f"[TTS] play failed: {type(e).__name__}: {e}")
