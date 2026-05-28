"""Doubao ASR (Speech-to-Text) module using WebSocket."""

import asyncio
import gzip
import json
import logging
import struct
import uuid
from typing import Optional

import websockets.legacy.client as ws_client
import websockets

import config
ASR_CONFIG = config.ASR_CONFIG

logger = logging.getLogger(__name__)


class DoubaoASR:
    """Doubao streaming ASR via WebSocket."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        resource_id: Optional[str] = None,
        url: Optional[str] = None,
    ):
        """Initialize ASR with config or overrides."""
        cfg = ASR_CONFIG
        self.api_key = api_key or cfg["api_key"]
        self.resource_id = resource_id or cfg["resource_id"]
        self.url = url or cfg["url"]
        self.ws: Optional[ws_client.connect] = None
        self._running = False
        self._receive_task: Optional[asyncio.Task] = None
        self._text_queue: asyncio.Queue[str] = asyncio.Queue()
        self._config_sent = False

    def _pack_request(self, payload: bytes, msg_type: int, flags: int = 0, compression: int = 1) -> bytes:
        """Pack a binary request frame."""
        b0 = 0x11
        b1 = (msg_type << 4) | flags
        b2 = (compression << 4) | 1
        b3 = 0x00
        header = bytes([b0, b1, b2, b3])
        payload_size = struct.pack(">I", len(payload))
        return header + payload_size + payload

    def _pack_full_request(self, config: dict) -> bytes:
        """Pack a config/request message."""
        json_bytes = json.dumps(config).encode("utf-8")
        compressed = gzip.compress(json_bytes)
        return self._pack_request(compressed, msg_type=1, flags=0)

    def _pack_audio(self, pcm_data: bytes, is_last: bool = False) -> bytes:
        """Pack an audio data frame."""
        flags = 0x02 if is_last else 0x00
        b0 = 0x11
        b1 = (2 << 4) | flags
        b2 = (0 << 4) | 0
        b3 = 0x00
        header = bytes([b0, b1, b2, b3])
        payload_size = struct.pack(">I", len(pcm_data))
        return header + payload_size + pcm_data

    def _parse_response(self, data: bytes) -> Optional[dict]:
        """Parse a binary response frame."""
        if len(data) < 8:
            return None

        msg_type_flags = (data[1] >> 4) & 0x0F
        has_sequence = (msg_type_flags & 0x01) != 0
        compression = (data[2] >> 4) & 0x0F

        if has_sequence:
            if len(data) < 12:
                return None
            payload_size = struct.unpack(">I", data[8:12])[0]
            payload = data[12:12 + payload_size]
        else:
            payload_size = struct.unpack(">I", data[4:8])[0]
            payload = data[8:8 + payload_size]

        if compression == 1:
            try:
                payload = gzip.decompress(payload)
            except Exception:
                pass
        try:
            return json.loads(payload)
        except Exception:
            return None

    async def connect(self) -> None:
        """Connect to Doubao ASR WebSocket."""
        headers = {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        self.ws = await ws_client.connect(self.url, extra_headers=headers)
        config = {
            "user": {"uid": "reachy_mini"},
            "audio": {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1},
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "result_type": "full",
            }
        }
        json_bytes = json.dumps(config).encode("utf-8")
        compressed = gzip.compress(json_bytes)
        frame = self._pack_request(compressed, msg_type=1, flags=0)
        await self.ws.send(frame)
        await asyncio.sleep(0.5)
        first_resp = await asyncio.wait_for(self.ws.recv(), timeout=5.0)
        logger.info(f"[ASR] Server response: {first_resp[:100]}")
        self._running = True
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("[ASR] Connected to Doubao WebSocket")

    async def _receive_loop(self) -> None:
        """Receive and parse ASR responses."""
        while self._running and self.ws:
            try:
                data = await self.ws.recv()
                resp = self._parse_response(data)
                logger.info(f"[ASR] Raw recv: {data[:100] if len(data) > 100 else data}")
                if resp:
                    result = resp.get("result", {})
                    text = result.get("text", "")
                    if text:
                        await self._text_queue.put(text)
                        logger.info(f"[ASR] Received text: {text}")
                    elif "error" in resp:
                        logger.error(f"[ASR] Error response: {resp['error']}")
            except websockets.exceptions.ConnectionClosed:
                logger.warning("[ASR] Connection closed by server")
                break
            except Exception as e:
                logger.error(f"[ASR] Receive error: {e}")
                break

    async def send_audio(self, pcm_chunk: bytes, is_last: bool = False) -> None:
        """Send audio data to ASR."""
        if self.ws:
            try:
                frame = self._pack_audio(pcm_chunk, is_last)
                logger.info(f"[ASR] Send audio: {len(pcm_chunk)} bytes, is_last={is_last}")
                await self.ws.send(frame)
            except websockets.exceptions.ConnectionClosed:
                logger.warning("[ASR] Connection closed, cannot send audio")
                raise

    async def get_text(self, timeout: float = 1.0) -> Optional[str]:
        """Get recognized text from queue."""
        try:
            return await asyncio.wait_for(self._text_queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        """Close the ASR connection."""
        self._running = False
        if self._receive_task:
            self._receive_task.cancel()
        try:
            if self.ws:
                await asyncio.wait_for(self.ws.close(), timeout=2.0)
        except Exception:
            pass
        self.ws = None
        logger.info("[ASR] Connection closed")
