"""Main entry point for Reachy Mini Conversation App - Voice Mode Only."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncio
import logging
import re
import time
from typing import Optional

import config
import asr
import brain
import tts
import audio
import robot
from actions.move_queue import MovementManager
from audio_animation.head_wobbler import HeadWobbler
from tools.core_tools import ToolDependencies


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ReachyConversationSystem:
    def __init__(self):
        self.audio: Optional[audio.ReachyAudioInput] = None
        self.brain: Optional[brain.DoubaoBrain] = None
        self.tts: Optional[tts.EdgeTTS] = None
        self.movement_manager: Optional[MovementManager] = None
        self.head_wobbler: Optional[HeadWobbler] = None

    async def initialize(self) -> None:
        logger.info("=" * 60)
        logger.info("  Reachy Mini Conversation System")
        logger.info("  Voice AI - Ready for Voice Input")
        logger.info("=" * 60)

        logger.info("[INIT] Initializing robot...")
        r = robot.get_shared_robot()
        logger.info("[INIT] Robot ready")

        logger.info("[INIT] Starting movement manager...")
        self.movement_manager = MovementManager(current_robot=r)
        self.movement_manager.start()
        logger.info("[INIT] Movement manager started")

        self.head_wobbler = HeadWobbler(set_speech_offsets=self.movement_manager.set_speech_offsets)
        self.head_wobbler.start()
        logger.info("[INIT] Head wobbler started")

        self.audio = audio.ReachyAudioInput()
        self.audio.start()
        logger.info("[INIT] Audio input ready")

        deps = ToolDependencies(
            reachy_mini=r,
            movement_manager=self.movement_manager,
            head_wobbler=self.head_wobbler,
        )

        self.brain = brain.DoubaoBrain(provider=config.BRAIN_CONFIG["provider"])
        self.brain.set_tool_dependencies(deps)
        logger.info("[INIT] Brain ready")

        self.tts = tts.EdgeTTS(voice=config.TTS_CONFIG["voice"])
        logger.info("[INIT] TTS ready")

        logger.info("[INIT] All components initialized")

    async def cleanup(self) -> None:
        logger.info("[CLEANUP] Shutting down...")

        if self.head_wobbler:
            self.head_wobbler.stop()

        if self.movement_manager:
            self.movement_manager.stop()

        if self.audio:
            self.audio.stop()

        logger.info("[CLEANUP] Shutdown complete")

    async def process_voice_input(self, text: str) -> None:
        print(f"\n[USER] {text}")

        result = await self.brain.query(text)
        print(f"[REACHY] {result.reply}")

        if result.reply:
            plain_reply = re.sub(r'（[^）]+）', '', result.reply).strip()
            plain_reply = re.sub(r'\([^)]+\)', '', plain_reply).strip()
            
            if plain_reply:
                tts_file = self.tts.speak_sync(plain_reply)
                if tts_file:
                    audio.play_tts_audio(tts_file)
            
            await self.brain.execute_actions_from_text(result.reply)

    async def voice_loop(self) -> None:
        retry_count = 0
        max_retries = 3
        retry_delay = 3.0

        while True:
            asr_client = asr.DoubaoASR()
            try:
                await asr_client.connect()
            except (ConnectionResetError, OSError) as e:
                logger.warning(f"[ASR] Connection failed: {e}, retry {retry_count+1}/{max_retries}")
                retry_count += 1
                await asyncio.sleep(retry_delay)
                if retry_count >= max_retries:
                    logger.error("[ASR] Max retries reached, giving up")
                    break
                continue

            print("\n[VOICE] 请说话...")
            speech_buffer = []
            start_time = time.time()
            deadline = start_time + 5.0
            speech_detected = False
            silence_after_speech = 0

            while time.time() < deadline:
                chunk = self.audio.read_chunk(0.01)
                if chunk:
                    max_val = max(
                        abs(int.from_bytes(chunk[i:i+2], 'little', signed=True))
                        for i in range(0, min(len(chunk), 100), 2)
                    )
                    is_speech = max_val > 500

                    if is_speech:
                        speech_detected = True
                        silence_after_speech = 0
                        speech_buffer.append(chunk)
                    elif speech_detected:
                        silence_after_speech += 1
                        if silence_after_speech < 20:
                            speech_buffer.append(chunk)
                        elif len(speech_buffer) > 20:
                            break
                await asyncio.sleep(0)

            if len(speech_buffer) < 20:
                print("[ASR] No speech detected, try again...")
                await asr_client.close()
                retry_count = 0
                await asyncio.sleep(1)
                continue

            full_audio = b"".join(speech_buffer)
            print(f"[ASR] Sending {len(full_audio)} bytes of audio...")
            await asr_client.send_audio(full_audio, is_last=True)
            await asyncio.sleep(0.5)

            full_text = ""
            while True:
                text = await asr_client.get_text(timeout=3.0)
                if text is None:
                    break
                if text.strip():
                    full_text = text.strip()

            await asr_client.close()

            if full_text:
                await self.process_voice_input(full_text)

            retry_count = 0

    async def run(self) -> None:
        await self.initialize()

        try:
            await self.voice_loop()
        except KeyboardInterrupt:
            print("\n\nInterrupted...")
        finally:
            await self.cleanup()


async def main() -> None:
    system = ReachyConversationSystem()
    await system.run()


if __name__ == "__main__":
    asyncio.run(main())