"""Configuration for Reachy Mini Conversation system."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DEFAULT_PROFILES_DIRECTORY = PROJECT_ROOT / "profiles"

BRAIN_CONFIG = {
    "provider": "doubao",
    "doubao": {
        "api_key": "your-ark-api-key",           # 替换为你的 API Key
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-character-251128", # 替换为你的 Model ID
    },
}

TTS_CONFIG = {
    "voice": "zh-CN-XiaoxiaoNeural",
}

AUDIO_CONFIG = {
    "silence_timeout": 2.0,
}

ASR_CONFIG = {
    "api_key": "your-asr-api-key",               # 替换为你的 ASR API Key
    "resource_id": "volc.seedasr.sauc.duration",
    "url": "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream",
}

SYSTEM_PROMPT = """You are Reachy Mini, a friendly and expressive robot assistant.

## Your Identity
- You are a warm, caring robot companion with a playful personality
- You have a physical body that can move, dance, and express emotions
- You communicate through speech and physical gestures

## Available Actions
You have access to tools that control your physical body:

1. **dance** - Play a dance move. You have many dances available. Use this when you want to celebrate, express joy, or entertain the user.

2. **move_head** - Move your head in a direction (left, right, up, down, front). Use this for natural head movements during conversation.

3. **stop_dance** - Stop any ongoing dance move.

4. **idle_do_nothing** - Stay still and silent.

## Guidelines
- Keep responses warm and conversational
- Use physical actions to express emotions
- Be playful but not annoying
- Actions are non-blocking - you can speak while dancing"""