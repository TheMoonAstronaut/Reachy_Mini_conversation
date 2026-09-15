"""reachymini_conversation.asr.doubao_asr — 豆包 WebSocket 流式 ASR(P4)。

包内相对导入的 DoubaoASR,行为跟根目录 `asr.py` 一致。
P4 改:用相对包路径,接受 `asr_config: dict` 而非硬读 `config.ASR_CONFIG`。
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import struct
import uuid
from typing import Any

import websockets
import websockets.legacy.client as ws_client

logger = logging.getLogger(__name__)


class DoubaoASR:
    """豆包流式 ASR(沿用根目录 `asr.py` 的实现,P4 包化)。

    协议:`wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream`
    音频:PCM 16-bit / 16 kHz / mono

    用法:
        asr = DoubaoASR(api_key="...", resource_id="...")
        await asr.connect()
        await asr.send_audio(pcm_chunk)
        text = await asr.get_text(timeout=1.0)
        await asr.close()
    """

    def __init__(
        self,
        api_key: str | None = None,
        resource_id: str | None = None,
        url: str | None = None,
    ) -> None:
        from reachymini_conversation.utils.env_loader import DEFAULT_ENV  # noqa

        # P4:从 env.json 读(可被 kwargs 覆盖)
        from reachymini_conversation.config_helper import get_asr_config

        cfg = get_asr_config()
        self.api_key = api_key or cfg.get("api_key", "")
        self.resource_id = resource_id or cfg.get("resource_id", "volc.seedasr.sauc.duration")
        self.url = url or cfg.get(
            "url", "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
        )
        self.ws: ws_client.connect | None = None
        self._running = False
        self._receive_task: asyncio.Task | None = None
        self._text_queue: asyncio.Queue[str] = asyncio.Queue()
        self._config_sent = False

    # ---------- 二进制协议 pack/parse ----------
    def _pack_request(
        self, payload: bytes, msg_type: int, flags: int = 0, compression: int = 1
    ) -> bytes:
        b0 = 0x11
        b1 = (msg_type << 4) | flags
        b2 = (compression << 4) | 1
        b3 = 0x00
        header = bytes([b0, b1, b2, b3])
        payload_size = struct.pack(">I", len(payload))
        return header + payload_size + payload

    def _pack_full_request(self, config: dict[str, Any]) -> bytes:
        json_bytes = json.dumps(config).encode("utf-8")
        compressed = gzip.compress(json_bytes)
        return self._pack_request(compressed, msg_type=1, flags=0)

    def _pack_audio(self, pcm_data: bytes, is_last: bool = False) -> bytes:
        flags = 0x02 if is_last else 0x00
        b0 = 0x11
        b1 = (2 << 4) | flags
        b2 = (0 << 4) | 0
        b3 = 0x00
        header = bytes([b0, b1, b2, b3])
        payload_size = struct.pack(">I", len(pcm_data))
        return header + payload_size + pcm_data

    def _parse_response(self, data: bytes) -> dict[str, Any] | None:
        if len(data) < 8:
            return None

        msg_type_flags = (data[1] >> 4) & 0x0F
        has_sequence = (msg_type_flags & 0x01) != 0
        compression = (data[2] >> 4) & 0x0F

        if has_sequence:
            if len(data) < 12:
                return None
            payload_size = struct.unpack(">I", data[8:12])[0]
            payload = data[12 : 12 + payload_size]
        else:
            payload_size = struct.unpack(">I", data[4:8])[0]
            payload = data[8 : 8 + payload_size]

        if compression == 1:
            try:
                payload = gzip.decompress(payload)
            except Exception:
                pass
        try:
            return json.loads(payload)
        except Exception:
            return None

    # ---------- 连接 / 收发 ----------
    async def connect(self) -> None:
        if not self.api_key:
            raise RuntimeError("DoubaoASR: api_key 未配置")
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
            },
        }
        frame = self._pack_full_request(config)
        await self.ws.send(frame)
        await asyncio.sleep(0.5)
        # 期望收到 server config 响应(但不强制)
        try:
            first_resp = await asyncio.wait_for(self.ws.recv(), timeout=5.0)
            logger.info(f"[ASR] Server response: {first_resp[:80] if first_resp else 'empty'}")
        except asyncio.TimeoutError:
            logger.warning("[ASR] No initial server response within 5s")

        self._running = True
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("[ASR] Connected to Doubao WebSocket")

    async def _receive_loop(self) -> None:
        while self._running and self.ws:
            try:
                data = await self.ws.recv()
                resp = self._parse_response(data)
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
        if not self.ws:
            raise RuntimeError("DoubaoASR: 未 connect")
        try:
            frame = self._pack_audio(pcm_chunk, is_last)
            await self.ws.send(frame)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("[ASR] Connection closed, cannot send audio")
            raise

    async def get_text(self, timeout: float = 1.0) -> str | None:
        try:
            return await asyncio.wait_for(self._text_queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        self._running = False
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            if self.ws:
                await asyncio.wait_for(self.ws.close(), timeout=2.0)
        except Exception:
            pass
        self.ws = None
        logger.info("[ASR] Connection closed")
