"""TTS 文本清洗:LLM 回复(Markdown)→ TTS 友好纯文本。

背景(P0-1 用户实测反馈):LLM 回复带 Markdown 结构符时,Edge TTS 会把
`**`、`_`、`#` 等符号原样念出来(中文语音里下划线被念成"下划线",极刺耳)。

策略(只去结构符,不丢内容):
  1. ``` 代码块:去 fence 与语言标注,保留内容(内容里的符号由规则 6 处理)
  2. [文字](url) / ![alt](url) → 文字 / alt(URL 不念)
  3. `inline code` → code
  4. 行首 # / > / 列表符 - * + → 去掉
  5. **粗** / *斜* / __粗__ / _斜_ / ~~删~~ → 保留文字
  5b. 残留不成对的 * / ＊ → 空格(否则被念"星号")
  6. 残留裸下划线(半角 _ / 全角 ＿ / ‗)一律 → 空格
     (snake_case 念成 "snake case";全角下划线 LLM 中文输出常见,同样被念)
  6b. Markdown 表格 | / ｜ → 空格(否则被念"竖线")
  7. 压缩多余空白

注意:本函数只服务 TTS 播报,不改变聊天窗里展示的原始 Markdown 文本。
"""

from __future__ import annotations

import re

_CODE_FENCE = re.compile(r"```\w*\n?(.*?)```", re.DOTALL)
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_HEADING = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_QUOTE = re.compile(r"^>[ \t]?", re.MULTILINE)
_BULLET = re.compile(r"^(\s*)[-*+]\s+", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3}|~~)(.+?)\1")
_STRAY_STAR = re.compile(r"[＊*]+")
_UNDERSCORE = re.compile(r"[＿‗_]+")
_TABLE_BAR = re.compile(r"[|｜]")
_MULTI_SPACE = re.compile(r"[ \t]+")
_MULTI_BLANK = re.compile(r"\n[ \t]*\n[ \t]*(\n[ \t]*)+")


def clean_text_for_tts(text: str | None) -> str:
    """LLM Markdown 回复 → TTS 播报用纯文本。空输入返回空串。"""
    if not text:
        return ""
    t = text
    t = _CODE_FENCE.sub(lambda m: m.group(1), t)  # 代码块:去 fence 留内容
    t = _MD_LINK.sub(r"\1", t)  # [文字](url) → 文字
    t = _INLINE_CODE.sub(r"\1", t)  # `code` → code
    t = _HEADING.sub("", t)
    t = _QUOTE.sub("", t)
    t = _BULLET.sub(r"\1", t)
    t = _EMPHASIS.sub(r"\2", t)  # **强调** → 强调
    t = _STRAY_STAR.sub(" ", t)  # 残留不成对的星号 → 空格
    t = _UNDERSCORE.sub(" ", t)  # 残留下划线(含全角 ＿) → 空格
    t = _TABLE_BAR.sub(" ", t)  # Markdown 表格竖线 → 空格
    t = _MULTI_SPACE.sub(" ", t)
    t = _MULTI_BLANK.sub("\n\n", t)
    return t.strip()
