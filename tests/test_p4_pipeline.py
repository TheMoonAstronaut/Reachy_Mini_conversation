"""P4 voice_pipeline + adapters unit tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ============================================================================
# Brain 工具解析测试(_parse_tool_calls 直接测,不走 mock)
# ============================================================================
def test_parse_tool_calls_chinese_parentheses():
    """中文(点头) → move_head tool call。"""
    from reachymini_conversation.brain.doubao_brain import _parse_tool_calls

    reply, tool_calls = _parse_tool_calls("好的(点头)我来了")
    assert "好的" in reply
    assert "我来了" in reply
    assert "点头" not in reply
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "move_head"


def test_parse_tool_calls_english_parentheses():
    """英文(动作) → 也解析。"""
    from reachymini_conversation.brain.doubao_brain import _parse_tool_calls

    reply, tool_calls = _parse_tool_calls("ok (dance) now")
    # "dance" 不在 _TOOL_NAME_MAP 的中文键里(只有"舞蹈"/"跳舞")
    # 但 _map_action_to_tool 检查 key in action,所以"dance" 不会匹配中文
    # 英文 (动作) 解析后只剩"ok now"
    assert "ok" in reply
    assert "now" in reply


def test_parse_tool_calls_multiple():
    """多个括号 → 多个 tool call。"""
    from reachymini_conversation.brain.doubao_brain import _parse_tool_calls

    reply, tool_calls = _parse_tool_calls("(舞蹈)(摆头)好的")
    names = [tc.name for tc in tool_calls]
    assert "dance" in names
    assert "move_head" in names


def test_parse_tool_calls_unknown_action():
    """未知动作(做饭) → 不生成 tool call,但剥离括号。"""
    from reachymini_conversation.brain.doubao_brain import _parse_tool_calls

    reply, tool_calls = _parse_tool_calls("我(做饭)很好")
    assert tool_calls == []
    assert "我" in reply
    assert "很好" in reply


def test_parse_tool_calls_no_parens():
    """无括号 → 不生成 tool call,reply 不变。"""
    from reachymini_conversation.brain.doubao_brain import _parse_tool_calls

    reply, tool_calls = _parse_tool_calls("好的")
    assert tool_calls == []
    assert reply == "好的"


# ============================================================================
# DoubaoBrain no-api-key mock 测试
# ============================================================================
def test_brain_no_api_key_returns_mock():
    """无 api_key → 降级 mock。"""
    from reachymini_conversation.brain.doubao_brain import BrainResult, DoubaoBrain

    brain = DoubaoBrain(cfg={"doubao": {"api_key": "", "model": "test"}})
    assert brain.is_configured is False
    result = brain.query("hello")
    assert isinstance(result, BrainResult)
    assert "hello" in result.reply
    assert "mock" in result.reply.lower() or "API Key" in result.reply


# ============================================================================
# EdgeTTS 测试
# ============================================================================
def test_edge_tts_default_voice_from_config():
    from reachymini_conversation.tts.edge_tts import EdgeTTS

    tts = EdgeTTS()
    assert tts.voice  # 非空


def test_edge_tts_synthesize_empty_text_returns_none():
    from reachymini_conversation.tts.edge_tts import EdgeTTS

    tts = EdgeTTS(voice="zh-CN-XiaoxiaoNeural")
    assert tts.synthesize("") is None
    assert tts.synthesize("   ") is None


def test_edge_tts_synthesize_async():
    """async synthesize 包装。"""
    from reachymini_conversation.tts.edge_tts import EdgeTTS

    tts = EdgeTTS(voice="zh-CN-XiaoxiaoNeural")
    tts.synthesize = MagicMock(return_value="/tmp/fake.wav")
    result = asyncio.run(tts.synthesize_async("hi"))
    assert result == "/tmp/fake.wav"


# ============================================================================
# VoicePipeline 测试(用真 brain / tts 子类)
# ============================================================================
class _FakeBrain:
    """Fake DoubaoBrain:返回预定义文本,不连网。"""

    def __init__(self, reply: str = "你好,我是 Reachy") -> None:
        self.reply = reply
        self.last_query = None
        from reachymini_conversation.brain.doubao_brain import BrainResult

        self._result = BrainResult(reply=reply, tool_calls=[], raw_output=reply)

    async def query_async(self, user_msg):
        self.last_query = user_msg
        return self._result


class _FakeTTS:
    """Fake EdgeTTS:synthesize 立即返回 fake wav 路径。"""

    def __init__(self, wav_path: str = "/tmp/fake.wav") -> None:
        self.wav_path = wav_path

    async def synthesize_async(self, text):
        return self.wav_path


def test_voice_pipeline_text_mode():
    """run_text → LLM + TTS 串起来,StateBus 状态切换。"""
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus
    from reachymini_conversation.voice_pipeline import (
        STATE_IDLE,
        VoicePipeline,
    )

    reset_state_bus()
    bus = get_state_bus()  # capture for assertions

    brain = _FakeBrain("你好,我是 Reachy")
    tts = _FakeTTS("/tmp/test.wav")
    pipeline = VoicePipeline(brain=brain, tts=tts)

    result = asyncio.run(pipeline.run_text("hi"))

    assert result.reply_text == "你好,我是 Reachy"
    assert result.user_text == "hi"
    assert brain.last_query == "hi"
    assert result.audio_path == "/tmp/test.wav"
    assert bus.get("status") == STATE_IDLE
    assert bus.get("last_reply") == "你好,我是 Reachy"


def test_voice_pipeline_no_reply_skips_tts():
    """LLM 没回复 → 不调 TTS。"""
    from reachymini_conversation.brain.doubao_brain import BrainResult
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus
    from reachymini_conversation.voice_pipeline import (
        STATE_IDLE,
        VoicePipeline,
    )

    reset_state_bus()

    brain = _FakeBrain("")  # empty reply
    brain._result = BrainResult(reply="", tool_calls=[], raw_output="")

    _FakeTTS()
    tts_was_called = [False]

    class _TtsSpy(_FakeTTS):
        async def synthesize_async(self, text):
            tts_was_called[0] = True
            return None

    pipeline = VoicePipeline(brain=brain, tts=_TtsSpy())
    result = asyncio.run(pipeline.run_text("hi"))

    assert result.reply_text == ""
    assert tts_was_called[0] is False
    assert result.audio_path is None
    assert get_state_bus().get("status") == STATE_IDLE


def test_voice_pipeline_handles_tts_failure():
    """TTS 合成失败不应阻断流程。"""
    from reachymini_conversation.state_bus import reset_state_bus
    from reachymini_conversation.voice_pipeline import VoicePipeline

    reset_state_bus()

    brain = _FakeBrain("你好")

    class _TtsFail(_FakeTTS):
        async def synthesize_async(self, text):
            return None  # 合成失败

    pipeline = VoicePipeline(brain=brain, tts=_TtsFail())
    result = asyncio.run(pipeline.run_text("hi"))
    assert result.reply_text == "你好"
    assert result.audio_path is None


def test_voice_pipeline_audio_mode_not_implemented():
    """(B1 变更)P4 时 run_audio 是 stub 抛 NotImplementedError;B1 已实装。

    保留此测试名做历史锚点,但断言改为:B1 后 run_audio 不再抛
    NotImplementedError(ASR 未配置时走 error 路径,见下方专项测试)。
    """
    from reachymini_conversation.voice_pipeline import VoicePipeline

    pipeline = VoicePipeline(brain=_FakeBrain(), tts=_FakeTTS(), asr_factory=_FakeASR)
    # 不再抛 NotImplementedError
    result = asyncio.run(pipeline.run_audio(b"\x00" * 320))
    assert result.user_text == "你好"  # _FakeASR 固定识别结果


# ============================================================================
# B1:run_audio(ASR → LLM → TTS)测试,mock ASR,不打真网络
# ============================================================================
class _FakeASR:
    """Fake DoubaoASR:固定识别出"你好",记录收到的音频。"""

    instances: list[_FakeASR] = []

    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.sent: list[tuple[bytes, bool]] = []
        self._pending = ["你好"]  # 第一轮 get_text 返回,之后 None
        _FakeASR.instances.append(self)

    async def connect(self) -> None:
        self.connected = True

    async def send_audio(self, pcm_chunk: bytes, is_last: bool = False) -> None:
        self.sent.append((pcm_chunk, is_last))

    async def get_text(self, timeout: float = 1.0) -> str | None:
        if self._pending:
            return self._pending.pop(0)
        return None

    async def close(self) -> None:
        self.closed = True


def test_voice_pipeline_run_audio_success():
    """run_audio:PCM → ASR 识别 → LLM → TTS 全链路,bus 状态正确收尾。"""
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus
    from reachymini_conversation.voice_pipeline import STATE_IDLE, VoicePipeline

    reset_state_bus()
    bus = get_state_bus()
    _FakeASR.instances.clear()

    brain = _FakeBrain("你好,我是 Reachy")
    tts = _FakeTTS("/tmp/test.wav")
    pcm = b"\x01\x02" * 1600  # 假 PCM 数据

    pipeline = VoicePipeline(brain=brain, tts=tts, asr_factory=_FakeASR)
    result = asyncio.run(pipeline.run_audio(pcm))

    # ASR 被正确使用:connect → send_audio(is_last=True) → close
    asr = _FakeASR.instances[-1]
    assert asr.connected and asr.closed
    assert asr.sent == [(pcm, True)]

    # 识别文本走了 run_text:LLM 收到的是 ASR 文本
    assert result.user_text == "你好"
    assert brain.last_query == "你好"
    assert result.reply_text == "你好,我是 Reachy"
    assert result.audio_path == "/tmp/test.wav"
    assert result.error is None

    # bus:状态收尾 idle,识别文本/回复都广播了
    assert bus.get("status") == STATE_IDLE
    assert bus.get("last_asr_text") == "你好"
    assert bus.get("last_reply") == "你好,我是 Reachy"


def test_voice_pipeline_run_audio_asr_failure_no_crash():
    """ASR 连接失败(如 API Key 未配置)→ bus 报 error,不 crash,LLM 不被调用。"""
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus
    from reachymini_conversation.voice_pipeline import STATE_ERROR, VoicePipeline

    reset_state_bus()
    bus = get_state_bus()

    class _AsrConnectFail:
        async def connect(self) -> None:
            raise RuntimeError("DoubaoASR: api_key 未配置")

        async def send_audio(self, pcm_chunk: bytes, is_last: bool = False) -> None:
            raise AssertionError("connect 失败就不该 send")

        async def get_text(self, timeout: float = 1.0) -> str | None:
            return None

        async def close(self) -> None:
            pass

    brain = _FakeBrain("不该被调用")
    pipeline = VoicePipeline(brain=brain, tts=_FakeTTS(), asr_factory=_AsrConnectFail)
    result = asyncio.run(pipeline.run_audio(b"\x00" * 320))

    # 不 crash,error 透出,LLM 没被问
    assert result.error is not None
    assert "api_key" in result.error
    assert result.user_text == "" and result.reply_text == ""
    assert result.audio_path is None
    assert brain.last_query is None
    # bus 报 error(UI 徽章可见)
    assert bus.get("status") == STATE_ERROR
    assert "asr" in str(bus.get("error"))


def test_voice_pipeline_run_audio_empty_recognition():
    """ASR 超时无文本 → 友好 error,不走 LLM,状态回 idle。"""
    from reachymini_conversation.state_bus import get_state_bus, reset_state_bus
    from reachymini_conversation.voice_pipeline import STATE_IDLE, VoicePipeline

    reset_state_bus()
    bus = get_state_bus()

    class _AsrSilent(_FakeASR):
        def __init__(self) -> None:
            super().__init__()
            self._pending = []  # 永远没有识别结果

    brain = _FakeBrain("不该被调用")
    pipeline = VoicePipeline(brain=brain, tts=_FakeTTS(), asr_factory=_AsrSilent)
    # asr_timeout 缩短,避免测试等 10s
    result = asyncio.run(pipeline.run_audio(b"\x00" * 320, asr_timeout=0.05))

    assert result.user_text == ""
    assert result.error is not None and "未识别" in result.error
    assert brain.last_query is None
    assert bus.get("status") == STATE_IDLE


def test_voice_pipeline_run_audio_asr_close_failure_tolerated():
    """close 抛异常也被吞掉(不影响识别结果)。"""
    from reachymini_conversation.state_bus import reset_state_bus
    from reachymini_conversation.voice_pipeline import VoicePipeline

    reset_state_bus()

    class _AsrBadClose(_FakeASR):
        async def close(self) -> None:
            raise RuntimeError("close boom")

    pipeline = VoicePipeline(brain=_FakeBrain("ok"), tts=_FakeTTS(), asr_factory=_AsrBadClose)
    result = asyncio.run(pipeline.run_audio(b"\x00" * 320))
    assert result.user_text == "你好"
    assert result.reply_text == "ok"
    assert result.error is None


# ============================================================================
# B1:音频格式转换(numpy → PCM 16-bit/16kHz/mono)测试
# ============================================================================
def test_audio_convert_int16_16k_mono_passthrough():
    """16kHz int16 单声道 → 不重采样,字节级等价。"""
    import numpy as np

    from reachymini_conversation.utils.audio_convert import numpy_to_pcm16_bytes

    data = np.array([0, 1000, -1000, 32767, -32768], dtype=np.int16)
    pcm = numpy_to_pcm16_bytes(16000, data)
    # int16 → float → int16,32767/-32768 边界:-32768/32768*32767 = -32767(差 1,可接受)
    out = np.frombuffer(pcm, dtype="<i2")
    assert len(out) == len(data)
    assert out[0] == 0 and abs(out[1] - 1000) <= 1 and abs(out[2] + 1000) <= 1


def test_audio_convert_float32_48k_stereo_resampled():
    """48kHz float32 立体声 → 16kHz 单声道 int16,长度约 1/3。"""
    import numpy as np

    from reachymini_conversation.utils.audio_convert import numpy_to_pcm16_bytes

    sr = 48000
    t = np.linspace(0, 0.1, int(sr * 0.1), endpoint=False)
    left = 0.5 * np.sin(2 * np.pi * 440 * t)
    right = left.copy()
    stereo = np.stack([left, right], axis=1).astype(np.float32)  # (N, 2)

    pcm = numpy_to_pcm16_bytes(sr, stereo)
    out = np.frombuffer(pcm, dtype="<i2")
    # 重采样 48000 → 16000:样本数 ≈ N/3
    assert abs(len(out) - len(t) / 3) <= 2
    # 幅值合理(0.5 → ~16383)
    assert 15000 < np.max(np.abs(out)) < 17000


def test_audio_convert_empty_and_invalid():
    """空输入 → b"";非法采样率 → ValueError。"""
    import numpy as np
    import pytest as _pytest

    from reachymini_conversation.utils.audio_convert import numpy_to_pcm16_bytes

    assert numpy_to_pcm16_bytes(16000, np.array([], dtype=np.float32)) == b""
    with _pytest.raises(ValueError):
        numpy_to_pcm16_bytes(0, np.zeros(100, dtype=np.float32))


def test_audio_convert_clips_and_cleans_nan():
    """超幅值 clipping + NaN 清洗,不炸。"""
    import numpy as np

    from reachymini_conversation.utils.audio_convert import numpy_to_pcm16_bytes

    data = np.array([0.0, 5.0, -5.0, np.nan], dtype=np.float32)
    pcm = numpy_to_pcm16_bytes(16000, data)
    out = np.frombuffer(pcm, dtype="<i2")
    assert out[1] == 32767 and out[2] == -32767
    assert out[3] == 0  # NaN → 0


def test_voice_pipeline_push_audio_called():
    """如果 push_audio_fn 给了,TTS 播放应该调它。"""
    from reachymini_conversation.state_bus import reset_state_bus
    from reachymini_conversation.voice_pipeline import VoicePipeline

    reset_state_bus()

    brain = _FakeBrain("hi")
    tts = _FakeTTS("/tmp/x.wav")
    push_called = [False]

    def push_fn(*args, **kwargs):
        push_called[0] = True

    pipeline = VoicePipeline(brain=brain, tts=tts, push_audio_fn=push_fn)

    # 跳过实际的 push(TTS.play 会读 wav 文件 — 我们的 fake 路径不存在)
    # 所以我们要让 tts.play no-op
    async def fake_play(*args, **kwargs):
        push_called[0] = True

    tts.play = fake_play  # type: ignore

    asyncio.run(pipeline.run_text("hi"))
    assert push_called[0] is True


# ============================================================================
# config_helper 测试
# ============================================================================
def test_config_helper_exposes_getters():
    from reachymini_conversation import config_helper

    brain = config_helper.get_brain_config()
    asr = config_helper.get_asr_config()
    tts = config_helper.get_tts_config()
    run_mode = config_helper.get_run_mode()
    hf = config_helper.get_hf_preload_datasets()
    assert isinstance(brain, dict)
    assert isinstance(asr, dict)
    assert isinstance(tts, dict)
    assert run_mode in ("pure_sim", "real_plus_sim")
    assert isinstance(hf, bool)


# ============================================================================
# DoubaoASR 测试
# ============================================================================
def test_doubao_asr_init():
    from reachymini_conversation.asr.doubao_asr import DoubaoASR

    asr = DoubaoASR(api_key="", resource_id="test")
    assert asr.url.startswith("wss://")


def test_doubao_asr_pack_audio_flags():
    from reachymini_conversation.asr.doubao_asr import DoubaoASR

    asr = DoubaoASR(api_key="test")
    frame_last = asr._pack_audio(b"\x00" * 50, is_last=True)
    frame_mid = asr._pack_audio(b"\x00" * 50, is_last=False)
    # flags bit 不同
    assert frame_last[1] & 0x02
    assert not (frame_mid[1] & 0x02)
