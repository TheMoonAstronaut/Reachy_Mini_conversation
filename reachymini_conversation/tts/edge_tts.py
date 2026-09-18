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

        用 edge-tts CLI(subprocess)。带重试(2026-09-18 实测:到
        speech.platform.bing.com 的 connect 间歇性失败,用户日志单轮
        失败率 ~50%,一失败该轮就没声音)——最多 2 次尝试,单次超时 20s。
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

        last_err = ""
        for attempt in (1, 2):
            try:
                subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=20.0,
                )
                self._normalize_loudness(output_file)
                logger.info(
                    f"[TTS] Generated ({self.voice}, 第{attempt}次尝试): {output_file}"
                )
                return output_file
            except subprocess.CalledProcessError as e:
                last_err = (e.stderr or "")[:200]
                logger.warning(f"[TTS] edge-tts 第{attempt}次失败: {last_err}")
            except subprocess.TimeoutExpired:
                last_err = f"timeout >20s (text: {text[:50]})"
                logger.warning(f"[TTS] edge-tts 第{attempt}次超时")
            except FileNotFoundError:
                # edge-tts CLI 没装(重试无意义)
                logger.error("[TTS] edge-tts CLI not found; install via `pip install edge-tts`")
                return None
        logger.error(f"[TTS] edge-tts 失败(已重试): {last_err}")
        return None

    @staticmethod
    def _normalize_loudness(path: str) -> None:
        """ffmpeg loudnorm 响度归一化(I=-10 LUFS / TP=-1.5dB);失败保留原文件。

        背景(2026-09-16 实测):Edge TTS 原始输出 mean_volume≈-21dB,
        真机扬声器物理音量小 + SDK 官方 EQ 在语音频段削 4~13dB
        (DEFAULT_SPEAKER_EQ_GAINS 负增益段),用户实测"几乎听不到"。
        I=-14(播客级)仍偏小,提到 I=-10(响口播级)再 +4dB。
        注意:不能用 edge-tts --volume 提升 —— loudnorm 以测量响度为目标,
        会把合成层的增益归一化抵消掉,必须直接调 loudnorm 目标。
        loudnorm 单程模式对语音播报足够。

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
            "-af", "loudnorm=I=-10:TP=-1.5:LRA=11",
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
            # 声道:SDK 播放 appsrc caps 固定 stereo(interleaved F32LE,
            # SDK audio_gstreamer.py:373 channels=2;base 的 PTS 时长计算
            # audio_base.py:149 用 data.shape[0] 当帧数)。
            # mono 直接 push 会被按 stereo 解释成一半帧数 → 2 倍速,
            # 且左右声道内容是 mono 的交错切片 → 纯混叠噪声
            # (2026-09-16 实测用户反馈"语速太快听不清",即此根因)。
            # (N,) → (N,2) 左右同值:C-order 字节序即 L0,R0,L1,R1,... 与
            # caps 的 interleaved 布局匹配,shape[0]=帧数让 PTS 归正。
            if data.ndim == 1:
                data = np.stack([data, data], axis=1)
            # 限幅(避免 clipping)
            data = np.clip(data, -1.0, 1.0).astype(np.float32)
            await asyncio.to_thread(push_audio_sample_fn, data)
            logger.debug(
                f"[TTS] Played {data.shape[0]} frames x {data.shape[1]}ch "
                f"@16000Hz(源 {samplerate}Hz)"
            )
        except Exception as e:
            logger.warning(f"[TTS] play failed: {type(e).__name__}: {e}")
