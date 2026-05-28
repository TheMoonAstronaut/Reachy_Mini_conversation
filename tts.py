"""Edge TTS (Text-to-Speech) module."""

import logging
import subprocess
from typing import Optional

import config
TTS_CONFIG = config.TTS_CONFIG

logger = logging.getLogger(__name__)


class EdgeTTS:
    """Microsoft Edge TTS wrapper."""

    def __init__(self, voice: Optional[str] = None):
        """Initialize TTS with voice."""
        self.voice = voice or TTS_CONFIG["voice"]

    def speak_sync(self, text: str, output_file: Optional[str] = None) -> Optional[str]:
        """Generate speech audio file synchronously."""
        if output_file is None:
            output_file = "temp_tts.wav"

        cmd = [
            "edge-tts",
            "--voice", self.voice,
            "--text", text,
            "--write-media", output_file,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info(f"[TTS] Generated: {output_file}")
            return output_file
        except subprocess.CalledProcessError as e:
            logger.error(f"[TTS] Error: {e.stderr}")
            return None
