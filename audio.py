"""Audio input/output module using Reachy Mini's麦克风阵列."""

import logging
import time
from typing import Optional, Tuple

import numpy as np
import soundfile as sf

import robot
get_shared_robot = robot.get_shared_robot

logger = logging.getLogger(__name__)


class ReachyAudioInput:
    """Audio input using Reachy Mini's built-in麦克风阵列."""

    def __init__(self):
        """Initialize audio input."""
        self.robot = None
        self.sample_rate = 16000

    def start(self) -> None:
        """Start audio recording."""
        self.robot = get_shared_robot()
        self.sample_rate = self.robot.media.get_input_audio_samplerate()
        logger.info(f"[AUDIO] Reachy recording available at {self.sample_rate} Hz")

    def stop(self) -> None:
        """Stop audio recording (no-op for shared robot)."""
        pass

    def read_chunk(self, chunk_duration: float = 0.01) -> Optional[bytes]:
        """Read a chunk of audio data."""
        if self.robot is None:
            return None
        audio_frame = self.robot.media.get_audio_sample()
        if audio_frame is None:
            return None

        audio_float32 = audio_frame.astype(np.float32)
        if audio_float32.ndim > 1:
            audio_float32 = audio_float32[:, 0]

        audio_int16 = (audio_float32 * 32767).astype(np.int16)
        return audio_int16.tobytes()

    async def record_until_speech_end(self, vad_timeout: float = 1.0) -> Optional[bytes]:
        """Record until speech ends (VAD trigger)."""
        import asyncio

        silence_start = None
        speech_buffer = []

        while True:
            chunk = self.read_chunk(0.2)
            if chunk:
                speech_buffer.append(chunk)
                silence_start = time.time()
            else:
                await asyncio.sleep(0.01)
                continue

            await asyncio.sleep(0.2)

            if silence_start and (time.time() - silence_start) > vad_timeout:
                if len(speech_buffer) > 3:
                    break

        return b"".join(speech_buffer) if speech_buffer else None

    def record_until_silence(
        self,
        silence_timeout: float = 2.0,
    ) -> Optional[Tuple[np.ndarray, int]]:
        """Record until silence is detected."""
        speech_chunks = []
        silence_duration = 0
        chunk_duration = 0.05
        min_audio_chunks = 20
        max_audio_chunks = 200
        silence_threshold = 0.01
        speech_threshold = 0.02
        min_speech_chunks = 10

        logger.info("[AUDIO] Waiting for speech...")

        start_time = time.time()
        speech_started = False

        while True:
            audio_frame = self.robot.media.get_audio_sample()

            if audio_frame is None:
                time.sleep(0.01)
                continue

            audio_float32 = audio_frame.astype(np.float32)

            if audio_float32.ndim > 1:
                rms = np.sqrt(np.mean(audio_float32[:, 0] ** 2))
            else:
                rms = np.sqrt(np.mean(audio_float32 ** 2))

            is_silence = rms < silence_threshold
            is_speech = rms > speech_threshold

            if is_speech:
                speech_started = True
                speech_chunks.append(audio_float32)
                silence_duration = 0
            elif is_silence:
                if speech_started:
                    silence_duration += chunk_duration
            else:
                if speech_started:
                    speech_chunks.append(audio_float32)

            if speech_started and silence_duration >= silence_timeout and len(speech_chunks) >= min_speech_chunks:
                elapsed = time.time() - start_time
                logger.info(f"[AUDIO] Silence detected after {elapsed:.1f}s, speech chunks: {len(speech_chunks)}")
                break

            if len(speech_chunks) >= max_audio_chunks:
                logger.info(f"[AUDIO] Max audio length reached ({len(speech_chunks)} chunks), stopping")
                break

            if time.time() - start_time > 30:
                logger.warning("[AUDIO] Timeout, stopping recording")
                break

        if not speech_chunks or len(speech_chunks) < min_speech_chunks:
            logger.warning(f"[AUDIO] Audio too short ({len(speech_chunks)} chunks), discarding")
            return None

        audio_data = np.concatenate(speech_chunks)
        logger.info(f"[AUDIO] Recorded {len(speech_chunks)} speech chunks, duration: {len(speech_chunks) * chunk_duration:.1f}s")
        return audio_data, self.sample_rate


def play_audio_with_reachy(
    audio_data: np.ndarray,
    sample_rate: int = 16000,
) -> None:
    """Play audio through Reachy Mini's speaker."""
    try:
        from scipy.signal import resample

        robot = get_shared_robot()
        output_sample_rate = robot.media.get_output_audio_samplerate()
        logger.info(f"[AUDIO] TTS sample rate: {sample_rate}, Reachy output: {output_sample_rate}")

        if audio_data.dtype == np.int16:
            audio_float = audio_data.astype(np.float32) / 32768.0
        else:
            audio_float = audio_data.astype(np.float32)

        if audio_float.ndim > 1:
            audio_float = audio_float[:, 0]

        if sample_rate != output_sample_rate:
            num_samples = int(len(audio_float) * output_sample_rate / sample_rate)
            if num_samples > 0:
                audio_float = resample(audio_float, num_samples)

        if audio_float.ndim == 1:
            audio_float = audio_float.reshape(-1, 1)

        logger.info(f"[AUDIO] Pushing {len(audio_float)} samples to Reachy speaker")
        robot.media.push_audio_sample(audio_float)
        logger.info("[AUDIO] Audio pushed successfully")
    except Exception as e:
        logger.error(f"[AUDIO] Reachy playback failed: {e}")
        import traceback
        traceback.print_exc()


def play_tts_audio(tts_file: str) -> None:
    """Play a TTS-generated audio file through Reachy."""
    try:
        import os
        logger.info(f"[TTS] TTS file: {tts_file}, exists: {os.path.exists(tts_file)}")
        audio_data, sr = sf.read(tts_file)
        logger.info(f"[TTS] Loaded audio: shape={audio_data.shape}, sr={sr}, dtype={audio_data.dtype}")
        play_audio_with_reachy(audio_data, sr)
    except ImportError:
        logger.warning("[AUDIO] soundfile not installed, skipping playback")
    except Exception as e:
        logger.error(f"[AUDIO] Playback error: {e}")
        import traceback
        traceback.print_exc()
