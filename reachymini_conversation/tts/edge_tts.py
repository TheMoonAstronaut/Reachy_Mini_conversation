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
        """
        try:
            import numpy as np
            import soundfile as sf

            data, samplerate = await asyncio.to_thread(sf.read, wav_path, dtype="float32")
            # 转 mono(机器人需要单声道)
            if data.ndim > 1:
                data = data.mean(axis=1)
            # 限幅(避免 clipping)
            data = np.clip(data, -1.0, 1.0).astype(np.float32)
            await asyncio.to_thread(push_audio_sample_fn, data)
            logger.debug(f"[TTS] Played {len(data)} samples @ {samplerate}Hz")
        except Exception as e:
            logger.warning(f"[TTS] play failed: {type(e).__name__}: {e}")
