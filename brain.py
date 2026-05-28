"""Doubao Brain - Intent parsing and tool calling module."""

import json
import logging
import re
import asyncio
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import httpx

import config
from config import BRAIN_CONFIG
from tools.core_tools import get_tool_specs, dispatch_tool_call, ToolDependencies


logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    name: str
    arguments: Dict[str, Any]


@dataclass
class BrainResult:
    reply: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw_output: str = ""


class DoubaoBrain:
    def __init__(self, provider: str = "doubao"):
        self.provider = provider
        self.cfg = BRAIN_CONFIG[self.provider]
        self.tool_deps: Optional[ToolDependencies] = None

    def set_tool_dependencies(self, deps: ToolDependencies) -> None:
        self.tool_deps = deps

    async def query(self, user_input: str) -> BrainResult:
        if self.provider == "doubao":
            return await self._query_doubao_with_tools(user_input)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

    async def _query_doubao_with_tools(self, user_input: str) -> BrainResult:
        headers = {
            "Authorization": f"Bearer {self.cfg['api_key']}",
            "Content-Type": "application/json",
        }

        system_prompt = self._get_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

        payload = {
            "model": self.cfg["model"],
            "messages": messages,
            "temperature": 0.7,
        }

        logger.info(f"[BRAIN] Request: model={payload['model']}")

        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                response = await client.post(
                    f"{self.cfg['base_url']}/chat/completions",
                    headers=headers,
                    json=payload,
                )
            except Exception as e:
                logger.error(f"[BRAIN] Request failed: {e}")
                return BrainResult(reply="抱歉，连接失败了。", raw_output=str(e))

            if response.status_code != 200:
                logger.error(f"[BRAIN] API error {response.status_code}: {response.text[:500]}")
                return BrainResult(reply="抱歉，服务暂时不可用。", raw_output=response.text)

            data = response.json()

            raw = data["choices"][0]["message"]
            assistant_content = raw.get("content", "")

            return BrainResult(
                reply=assistant_content.strip() if assistant_content else "",
                raw_output=json.dumps(raw),
            )

    async def execute_actions_from_text(self, text: str) -> None:
        """Parse action descriptions in parentheses and execute them."""
        if not self.tool_deps:
            return

        action_pattern = r'（([^）]+)）'
        matches = re.findall(action_pattern, text)
        
        for action in matches:
            action = action.strip()
            logger.info(f"[BRAIN] Found action in text: {action}")
            
            action_lower = action.lower()
            
            if '跳舞' in action or '舞' in action or '跳' in action or 'dance' in action_lower:
                asyncio.create_task(self._execute_dance())
                continue
            
            if '歪' in action and '头' in action:
                asyncio.create_task(self._execute_head_move('left'))
                continue
                
            if '点' in action and '头' in action:
                asyncio.create_task(self._execute_head_move('front'))
                continue
                
            if '摇' in action and '头' in action:
                asyncio.create_task(self._execute_head_move('right'))
                continue
                
            if '看' in action or '望' in action or '注视' in action:
                if '左' in action:
                    asyncio.create_task(self._execute_head_move('left'))
                elif '右' in action:
                    asyncio.create_task(self._execute_head_move('right'))
                elif '上' in action or '天空' in action:
                    asyncio.create_task(self._execute_head_move('up'))
                elif '下' in action or '地' in action:
                    asyncio.create_task(self._execute_head_move('down'))
                else:
                    asyncio.create_task(self._execute_head_move('front'))
                continue
                    
            if '抬' in action and '头' in action:
                asyncio.create_task(self._execute_head_move('up'))
                continue
                
            if '低' in action and '头' in action:
                asyncio.create_task(self._execute_head_move('down'))
                continue
                
            if '转' in action and '头' in action:
                if '左' in action:
                    asyncio.create_task(self._execute_head_move('left'))
                elif '右' in action:
                    asyncio.create_task(self._execute_head_move('right'))
                continue
                    
            if '晃' in action and '头' in action:
                asyncio.create_task(self._execute_head_move('right'))
                continue
                
            if '摇头' in action or '摆头' in action:
                asyncio.create_task(self._execute_head_move('right'))
                continue

    async def _execute_dance(self, dance_type: str = None) -> None:
        if not self.tool_deps:
            return
        from tools.core_tools import dispatch_tool_call
        
        args = {}
        if dance_type:
            from tools.dance import AVAILABLE_MOVES
            for move_name in AVAILABLE_MOVES:
                if dance_type.lower() in move_name.lower():
                    args['move'] = move_name
                    break
        
        try:
            await dispatch_tool_call("dance", json.dumps(args), self.tool_deps)
        except Exception as e:
            logger.error(f"[BRAIN] Dance execution failed: {e}")

    async def _execute_head_move(self, direction: str) -> None:
        if not self.tool_deps:
            return
        from tools.core_tools import dispatch_tool_call
        try:
            await dispatch_tool_call("move_head", json.dumps({"direction": direction}), self.tool_deps)
        except Exception as e:
            logger.error(f"[BRAIN] Head move execution failed: {e}")

    def _get_system_prompt(self) -> str:
        try:
            profile_dir = config.DEFAULT_PROFILES_DIRECTORY / "default"
            instructions_file = profile_dir / "instructions.txt"
            if instructions_file.exists():
                return instructions_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"Failed to load profile instructions: {e}")

        return config.SYSTEM_PROMPT