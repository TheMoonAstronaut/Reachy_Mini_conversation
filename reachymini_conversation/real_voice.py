"""reachymini_conversation.real_voice — 真机麦克风免提语音环路(V2.4)。

职责(SPEC_V2.md D4):
  real_plus_sim + 语音模式时,用 Reachy Mini 自带麦克风做免提连续对话:
    real_mini.media.get_audio_sample() → EnergyVAD 分句 → pipeline.run_audio()
    → TTS 经 push_audio_fn 路由到真机扬声器(real_mini.media.push_audio_sample)

激活条件(全部满足才采音):
  - bus.run_mode == "real_plus_sim" 且 orchestrator.real_mini 在线
  - bus.chat_mode == "voice"(UI 切换时写入)

音源跟随(V2 Fix C,有线版实测确认 --no-media 下 SDK 媒体不可用):
  pure_sim + voice           → 浏览器麦克风(web_ui 流式组件,不经本模块)
  real_plus_sim(有线)+ voice → **本机 USB 声卡**(local_audio.GStreamerAudio 直连)
  real_plus_sim(无线)+ voice → real_mini.media(机器人侧 daemon 媒体链)

线程模型:
  单后台 daemon 线程;阻塞 read 用 SDK 返回节奏;轮次执行(异步 pipeline)
  用线程内 asyncio.run() 新建 loop(ASR/LLM/TTS 都是网络调用,互不影响;
  state_bus 线程安全,徽章状态照常广播)。
  已知限制:轮次结果不写 Chatbot(后台线程无法推 Gradio 会话状态),
  对话内容靠真机扬声器 + 顶部状态徽章呈现;记录增强留 V2.x。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from reachymini_conversation.state_bus import get_state_bus
from reachymini_conversation.utils.audio_convert import numpy_to_pcm16_bytes
from reachymini_conversation.voice_loop import EnergyVAD

logger = logging.getLogger(__name__)


class RealVoiceLoop:
    """真机麦克风免提环路(后台线程)。"""

    def __init__(
        self,
        orchestrator: Any,
        pipeline: Any,
        *,
        idle_sleep_s: float = 0.2,
        vad: Any = None,
    ) -> None:
        self._orch = orchestrator
        self._pipeline = pipeline
        self._idle_sleep_s = idle_sleep_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # 默认自适应底噪 VAD(2026-09-16:固定阈值被环境底噪击穿);
        # 测试可注入固定阈值 EnergyVAD(rms_threshold=...) 跳过学习期。
        self._vad = vad if vad is not None else EnergyVAD()
        # 防声学反馈:真机扬声器播放 TTS 期间,麦克风会采到自己的声音 →
        # 再识别再回复 → 无限自言自语循环。播放期 + 余量内丢帧不喂 VAD。
        self._mute_until = 0.0

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="real-voice-loop"
        )
        self._thread.start()
        logger.info("[real-voice] 线程已启动(待命:real_plus_sim + voice 模式时采音)")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    # ---------- 主循环 ----------
    def _should_listen(self, bus: Any) -> bool:
        return (
            self._orch.run_mode == "real_plus_sim"
            and self._orch.real_mini is not None
            and bus.get("chat_mode", "text") == "voice"
        )

    def _audio_source(self, bus: Any) -> tuple[Any | None, str]:
        """按连接类型选音源:(source_obj, 'wired'|'wireless')。

        wired:本机 USB 声卡(local_audio 单例,GStreamerAudio);
        wireless:real_mini.media(机器人 daemon 媒体链)。
        """
        if bus.get("real_conn_type", "wired") == "wireless":
            media = getattr(self._orch.real_mini, "media", None)
            return media, "wireless"
        from reachymini_conversation.local_audio import get_local_audio

        return get_local_audio(), "wired"

    def _loop(self) -> None:
        bus = get_state_bus()
        sample_rate: int | None = None
        src = None  # 当前激活的音源(用于 stop_recording)
        src_kind = ""
        while not self._stop.is_set():
            if not self._should_listen(bus):
                if src is not None:
                    # 退出聆听状态:停录音
                    self._teardown_source(src, src_kind)
                    src, sample_rate, src_kind = None, None, ""
                    bus.update("real_voice_active", False)
                time.sleep(self._idle_sleep_s)
                continue

            try:
                if src is None:
                    src, src_kind = self._audio_source(bus)
                    if src is None:
                        bus.update("real_voice_active", False)
                        time.sleep(1.0)  # 声卡不在,慢速重试
                        continue
                    sample_rate = int(src.get_input_audio_samplerate())
                    src.start_recording()
                    logger.info(f"[real-voice] 音源={src_kind} {sample_rate}Hz,开始聆听")
                    bus.update("real_voice_active", True)
                    self._vad.reset()

                chunk = src.get_audio_sample()  # float32 ndarray | None,(N,)或(N,C)
                if chunk is None or len(chunk) == 0:
                    time.sleep(0.01)
                    continue
                # TTS 播放期(含余量)丢帧:防"采到自己的声音→再回复"的反馈循环
                if time.monotonic() < self._mute_until:
                    continue
                seg = self._vad.push(sample_rate, chunk)
                if seg is None:
                    continue

                # 完整语句 → 跑一轮 pipeline(TTS 由 push_audio_fn 路由到真机)
                pcm = numpy_to_pcm16_bytes(sample_rate, seg)
                if not pcm:
                    continue
                logger.info(f"[real-voice] 语句 {len(seg)/sample_rate:.1f}s → ASR/LLM/TTS")
                try:
                    result = asyncio.run(self._pipeline.run_audio(pcm))
                except Exception as e:
                    logger.exception(f"[real-voice] 轮次执行失败: {e}")
                    bus.update("error", f"real_voice: {type(e).__name__}: {e}")
                    result = None
                self._vad.reset()
                # 轮次回写 bus → UI tick 消费后 append 到 chatbot
                # (2026-09-16 用户反馈:real+voice 模式对话框无任何文本反馈,
                # 识别/回复只在后台跑,用户看不见自己说了什么、机器人答了什么)
                if result is not None and getattr(result, "user_text", ""):
                    bus.update("voice_turn_seq", (bus.get("voice_turn_seq") or 0) + 1)
                    bus.update(
                        "voice_turn",
                        {
                            "user": result.user_text,
                            "reply": getattr(result, "reply_text", "") or "(无回复)",
                        },
                    )
                # 估算播放时长,期间静音麦克风(run_audio 返回时播放刚开始)
                if result is not None and getattr(result, "audio_path", None):
                    play_s = _wav_seconds(result.audio_path)
                    if play_s > 0:
                        self._mute_until = time.monotonic() + play_s + 0.4
                        logger.debug(f"[real-voice] TTS 播放 {play_s:.1f}s,麦克风静音至 +{play_s+0.4:.1f}s")
            except Exception as e:
                logger.warning(f"[real-voice] 采音异常: {type(e).__name__}: {e}")
                time.sleep(0.5)
        if src is not None:
            self._teardown_source(src, src_kind)
        bus.update("real_voice_active", False)
        logger.info("[real-voice] 线程退出")

    @staticmethod
    def _teardown_source(src: Any, kind: str) -> None:
        try:
            src.stop_recording()
            logger.info(f"[real-voice] 停止录音({kind})")
        except Exception as e:
            logger.debug(f"[real-voice] stop_recording 异常(忽略): {e}")


def _wav_seconds(path: str) -> float:
    """wav 文件时长(秒);读不出来返回 0(不静音)。"""
    try:
        import soundfile as sf

        info = sf.info(path)
        if info.samplerate > 0:
            return float(info.frames) / float(info.samplerate)
    except Exception:
        pass
    return 0.0


# ============================================================================
# TTS 音频路由(按当前 run_mode 推到 sim / real 的扬声器)
# ============================================================================
def make_tts_audio_router(orchestrator_getter: Any):
    """构造 push_audio_fn:运行时按 orchestrator.run_mode 路由。

    - real_plus_sim 且 real 在线:
        - 有线 → **本机 USB 声卡**(local_audio,daemon B --no-media 不走媒体链)
        - 无线 → real_mini.media(机器人侧 daemon 播放)
    - 其余 → sim(音频驱动 wobbling 联动;浏览器侧照常播报)
    单例内做播放管线懒启动(start_playing 一次)。
    """
    _playing_started = False

    def route(pcm: Any) -> None:
        nonlocal _playing_started
        orch = orchestrator_getter()
        if orch is None:
            return

        target = None
        local_audio = None
        if orch.run_mode == "real_plus_sim" and orch.real_mini is not None:
            bus = get_state_bus()
            if bus.get("real_conn_type", "wired") == "wireless":
                target = orch.real_mini
            else:
                from reachymini_conversation.local_audio import get_local_audio

                local_audio = get_local_audio()
        elif orch.sim_mini is not None:
            target = orch.sim_mini

        try:
            if local_audio is not None:
                if not _playing_started:
                    local_audio.start_playing()
                    _playing_started = True
                local_audio.push_audio_sample(pcm)
            elif target is not None:
                target.media.push_audio_sample(pcm)
        except Exception as e:
            logger.warning(f"[tts-router] push_audio_sample({orch.run_mode}) 失败: {e}")

    return route
