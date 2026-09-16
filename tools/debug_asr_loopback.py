"""tools/debug_asr_loopback.py — ASR 链路环回验证(P0-2 排错用,CI 不跑)。

问题现场:浏览器录音 20s(44.1kHz→16k 重采样)送豆包 ASR,
服务器 1 秒就 "Connection closed by server",空结果。
真机麦(16kHz 直采)同样代码路径却识别成功。

本脚本做决定性区分实验:
  edge-tts 合成已知文本 → wav → 重采样到 16k PCM → 直接喂
  voice_pipeline.run_audio() → 看豆包识别结果。

  - 识别出文字 → ASR 链路/协议/参数全 OK,问题在浏览器录音内容(音量/设备)
  - 仍被秒关 → 协议层问题(录音路径与真机路径的 pcm 有结构差异)

用法:unset 代理后  python tools/debug_asr_loopback.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


async def main() -> int:
    import numpy as np

    from reachymini_conversation.asr.doubao_asr import DoubaoASR
    from reachymini_conversation.tts.edge_tts import EdgeTTS
    from reachymini_conversation.voice_pipeline import VoicePipeline

    # 1. 合成已知内容 wav(EdgeTTS 无参自动读 env 的 voice 配置)
    tts = EdgeTTS()
    wav = await tts.synthesize_async("你好,我是机器人,测试语音识别。")
    print(f"[1] TTS 合成: {wav}")
    if not wav:
        print("TTS 失败")
        return 1

    # 2. ffmpeg 转 16k mono s16le PCM(Edge TTS 输出实为 MP3,scipy 读不了)
    import subprocess

    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", wav, "-ar", "16000", "-ac", "1", "-f", "s16le", "-"],
        capture_output=True,
        check=True,
    )
    pcm = proc.stdout
    import numpy as np

    pcm_rms = float(
        np.sqrt(np.mean(np.frombuffer(pcm, dtype="<i2").astype("float32") ** 2))
    )
    print(f"[2] pcm: {len(pcm)} bytes ({len(pcm) / 2 / 16000:.2f}s @16k), rms={pcm_rms:.1f}")
    numpy_to_pcm16_bytes = None  # 本路径不再用(留 import 防 IDE 报未用)

    # 3. 走 pipeline.run_audio(brain/tts 用假的,只验证到 ASR 步)
    class _FakeBrain:
        async def query_async(self, text, tool_deps=None):
            class R:
                reply = "(环回测试,不调 LLM)"
                tool_calls: list = []

            print(f"[5] ASR 识别结果: {text!r}")
            return R()

    class _NullTTS:
        async def synthesize_async(self, text):
            return None

        async def play(self, *a, **k):
            pass

    pipeline = VoicePipeline(
        brain=_FakeBrain(),
        tts=_NullTTS(),
        asr_factory=DoubaoASR,  # 无参构造,自动从 env.json 读 key
    )
    print("[4] 送 ASR …")
    result = await pipeline.run_audio(pcm)
    print(f"[6] user_text={result.user_text!r} error={result.error!r}")
    ok = bool(result.user_text) and not result.error
    print("===> ASR 链路正常 ✅" if ok else "===> ASR 链路异常 ❌(协议层问题)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
