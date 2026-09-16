"""reachymini_conversation.voice_pipeline — 语音管线协调器(P4 决策 6,B1 补全)。

协调 ASR → LLM → TTS 三段,通过 StateBus 广播状态。

B1(P4 闭环)范围:
  - run_text:文本 → 豆包 LLM(function calling)→ Edge TTS
  - run_audio:PCM(16-bit/16kHz/mono)→ DoubaoASR 识别 → 复用 run_text
  - StateBus 事件:listening / thinking / speaking / playing / idle / error
  - ASR 异常全部 try/except 包裹:bus 报 error,不 crash
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from reachymini_conversation.state_bus import get_state_bus

logger = logging.getLogger(__name__)


# 状态(同步 state_bus)
STATE_IDLE = "idle"
STATE_LISTENING = "listening"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"
STATE_PLAYING = "playing"
STATE_ERROR = "error"


@dataclass
class PipelineResult:
    """一次完整 voice/text 轮次的结果。"""

    user_text: str
    reply_text: str
    tool_calls: list[Any]
    audio_path: str | None  # TTS wav 路径(若合成成功)
    error: str | None = None  # 轮次级错误(ASR 失败等),None = 正常


class VoicePipeline:
    """语音管线协调器。

    用法:
        pipeline = VoicePipeline(
            brain=brain,
            tts=tts,
            push_audio_fn=reachy.media.push_audio_sample,  # 可选
            asr_factory=DoubaoASR,                          # 可选,默认豆包 ASR
            tool_deps=tool_deps,                            # 可选,LLM 真 function calling
        )

        # 文本模式(UI Chatbot)
        result = await pipeline.run_text("你好")

        # 音频模式(B1:浏览器麦克风 → PCM → ASR → LLM → TTS)
        result = await pipeline.run_audio(pcm_bytes)
    """

    def __init__(
        self,
        brain: Any,
        tts: Any,
        push_audio_fn: Callable[[Any], Any] | None = None,
        asr_factory: Callable[[], Any] | None = None,
        tool_deps: Any = None,
    ) -> None:
        self.brain = brain
        self.tts = tts
        self.push_audio_fn = push_audio_fn
        # asr_factory:每轮语音新建一个 ASR 实例(DoubaoASR 单次连接语义)
        if asr_factory is None:
            from reachymini_conversation.asr.doubao_asr import DoubaoASR

            asr_factory = DoubaoASR
        self.asr_factory = asr_factory
        # tool_deps:None 时按老签名调 brain.query_async(user_msg)(兼容测试 Fake)
        self.tool_deps = tool_deps
        self.bus = get_state_bus()

    # ---------- 主流程 ----------
    async def run_text(self, user_text: str) -> PipelineResult:
        """文本模式(Chatbot 输入直接走 LLM + TTS)。

        P4 替代 mock LLM,接通豆包 + Edge TTS。
        """
        self.bus.update("status", STATE_THINKING)
        self.bus.update("error", None)  # 新轮次清掉旧错误
        self.bus.update("last_user_text", user_text)

        # 1. LLM 查询
        brain_result = await self._query_brain(user_text)
        reply = brain_result.reply
        tool_calls = brain_result.tool_calls

        self.bus.update("status", STATE_SPEAKING if reply else STATE_IDLE)
        self.bus.update("last_reply", reply)
        self.bus.update(
            "last_tool_calls",
            [
                {"name": tc.name, "arguments": tc.arguments, "result": tc.result}
                for tc in tool_calls
            ],
        )

        # 2. TTS 合成 + 播放
        audio_path: str | None = None
        if reply:
            await self._speak(reply)
            audio_path = self.bus.get("last_audio_path")

        self.bus.update("status", STATE_IDLE)
        return PipelineResult(
            user_text=user_text,
            reply_text=reply,
            tool_calls=tool_calls,
            audio_path=audio_path,
        )

    async def run_audio(self, pcm_bytes: bytes, asr_timeout: float = 10.0) -> PipelineResult:
        """音频模式(B1):DoubaoASR 识别 → 复用 run_text 走 LLM → TTS。

        流程:connect → send_audio(pcm, is_last=True) → 轮询 get_text 收最终文本 → close。
        ASR 任何异常都被捕获:bus 报 error(UI 徽章可见),返回带 error 的 PipelineResult,不 crash。

        Args:
            pcm_bytes: PCM 16-bit / 16kHz / mono 字节(调用方负责转换)
            asr_timeout: 等最终识别文本的总超时(秒)
        """
        self.bus.update("status", STATE_LISTENING)
        self.bus.update("error", None)  # 新轮次清掉旧错误

        asr = self.asr_factory()
        text = ""
        asr_error: str | None = None
        try:
            await asr.connect()
            await asr.send_audio(pcm_bytes, is_last=True)
            text = await self._collect_final_text(asr, timeout=asr_timeout)
        except Exception as e:
            logger.exception("[Pipeline] ASR 识别失败")
            asr_error = f"{type(e).__name__}: {e}"
        finally:
            # close 永远尝试,且不能再抛
            try:
                await asr.close()
            except Exception as e:
                logger.warning(f"[Pipeline] ASR close 失败(忽略): {e}")

        if asr_error is not None:
            self.bus.update("status", STATE_ERROR)
            self.bus.update("error", f"asr: {asr_error}")
            return PipelineResult(
                user_text="",
                reply_text="",
                tool_calls=[],
                audio_path=None,
                error=asr_error,
            )

        if not text.strip():
            # 识别成功但没有文本(静音/太短)
            msg = "未识别到有效语音"
            logger.info(f"[Pipeline] ASR 空结果: {msg}")
            self.bus.update("status", STATE_IDLE)
            self.bus.update("error", f"asr: {msg}")
            return PipelineResult(
                user_text="",
                reply_text="",
                tool_calls=[],
                audio_path=None,
                error=msg,
            )

        logger.info(f"[Pipeline] ASR 识别文本: {text!r}")
        self.bus.update("last_asr_text", text)
        return await self.run_text(text)

    # ---------- ASR 内部 ----------
    async def _collect_final_text(self, asr: Any, timeout: float) -> str:
        """轮询 asr.get_text,收最终识别文本。

        豆包协议 result_type=full:每次推送都是**全量**文本,
        所以策略是"取最后一个非空结果";已有结果后 1s 无更新即认为稳定。
        """
        deadline = time.monotonic() + timeout
        final_text = ""
        while time.monotonic() < deadline:
            chunk = await asr.get_text(timeout=1.0)
            if chunk:
                final_text = chunk  # 全量覆盖,保留最新
            elif final_text:
                break  # 文本稳定(1s 无新推送)
        return final_text

    async def _query_brain(self, user_text: str) -> Any:
        """调 brain.query_async;有 tool_deps 时透传(真 function calling)。"""
        if self.tool_deps is not None:
            return await self.brain.query_async(user_text, tool_deps=self.tool_deps)
        return await self.brain.query_async(user_text)

    # ---------- TTS 内部 ----------
    async def _speak(self, text: str) -> None:
        """Edge TTS 合成 + 推到机器人(sim / 真机)。

        合成前先 clean_text_for_tts:LLM 回复是 Markdown,直接送 TTS 会把
        `**`、`_` 等结构符念出来(下划线被念成"下划线",P0-1 实测痛点)。
        """
        from reachymini_conversation.utils.text_clean import clean_text_for_tts

        text = clean_text_for_tts(text)
        if not text:
            logger.info("[Pipeline] 清洗后无可播报文本,跳过 TTS")
            return
        try:
            self.bus.update("status", STATE_PLAYING)

            # 1. 合成 wav
            wav_path = await self.tts.synthesize_async(text)
            if not wav_path:
                logger.warning("[Pipeline] TTS synthesize failed, no audio played")
                return

            self.bus.update("last_audio_path", wav_path)

            # 2. 推到机器人(可选)
            if self.push_audio_fn is not None:
                await self.tts.play(wav_path, self.push_audio_fn)
            else:
                logger.debug("[Pipeline] no push_audio_fn, TTS wav not played")

        except Exception as e:
            logger.exception(f"[Pipeline] _speak failed: {e}")
            self.bus.update("status", STATE_ERROR)
            self.bus.update("error", f"tts: {e}")
