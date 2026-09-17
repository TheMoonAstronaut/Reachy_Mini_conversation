"""TTS 文本清洗测试(utils.text_clean.clean_text_for_tts)。

保护的痛点(P0-1 实测):TTS 把 Markdown 下划线/星号原样念出来。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from reachymini_conversation.utils.text_clean import clean_text_for_tts  # noqa: E402


class TestEmphasis:
    def test_bold(self) -> None:
        assert clean_text_for_tts("这是 **重点** 内容") == "这是 重点 内容"

    def test_italic_underscore(self) -> None:
        assert clean_text_for_tts("这是 _斜体_ 词") == "这是 斜体 词"

    def test_strikethrough(self) -> None:
        assert clean_text_for_tts("~~旧方案~~ 用新方案") == "旧方案 用新方案"

    def test_snake_case_no_underscore_spoken(self) -> None:
        """核心痛点:标识符里的下划线不得念出('下划线'三字)。"""
        out = clean_text_for_tts("我调用了 move_head 工具")
        assert "_" not in out
        assert "move head" in out


class TestFullWidthAndStraySymbols:
    """回归(2026-09-16 用户实测复念):全角下划线 / 表格竖线 / 残留星号。

    LLM 中文输出常用全角 ＿(U+FF3F),Edge TTS 同样念"下划线";
    Markdown 表格的 | 被念"竖线";不成对的 * 被念"星号"。
    """

    def test_fullwidth_underscore_removed(self) -> None:
        out = clean_text_for_tts("字段名是 user＿name＿v2")
        assert "＿" not in out
        assert "user name v2" in out

    def test_table_bars_removed(self) -> None:
        out = clean_text_for_tts("| 名称 | 数量 |\n| --- | --- |\n| 苹果 | 3 |")
        assert "|" not in out
        assert "名称" in out and "苹果" in out

    def test_fullwidth_table_bar_removed(self) -> None:
        out = clean_text_for_tts("数值｜单位")
        assert "｜" not in out
        assert "数值" in out and "单位" in out

    def test_stray_star_removed(self) -> None:
        """成对的 **粗** 由 emphasis 规则保留文字;单个残留 * 不得念出。"""
        out = clean_text_for_tts("注意 * 重要 * 全角 ＊ 也一样")
        assert "*" not in out and "＊" not in out
        assert "重要" in out

    def test_paired_emphasis_still_keeps_text(self) -> None:
        """防回归:增强规则不得破坏成对强调的文字保留。"""
        out = clean_text_for_tts("这是 **重点** 与 _斜体_")
        assert "重点" in out and "斜体" in out


class TestMarkdownStructures:
    def test_link(self) -> None:
        assert clean_text_for_tts("[点击这里](https://example.com) 查看") == "点击这里 查看"

    def test_heading(self) -> None:
        assert clean_text_for_tts("## 小节标题\n正文") == "小节标题\n正文"

    def test_bullet(self) -> None:
        assert clean_text_for_tts("- 第一项\n- 第二项") == "第一项\n第二项"

    def test_inline_code(self) -> None:
        assert clean_text_for_tts("运行 `start.sh` 即可") == "运行 start.sh 即可"

    def test_code_fence_keeps_content(self) -> None:
        out = clean_text_for_tts("```python\nprint_hi()\n```")
        assert "print hi()" in out
        assert "`" not in out
        assert "_" not in out


class TestEdgeCases:
    def test_empty_and_none(self) -> None:
        assert clean_text_for_tts("") == ""
        assert clean_text_for_tts(None) == ""

    def test_plain_text_unchanged(self) -> None:
        assert clean_text_for_tts("你好,我是 Reachy Mini。") == "你好,我是 Reachy Mini。"

    def test_whitespace_collapsed(self) -> None:
        assert clean_text_for_tts("多  余   空格") == "多 余 空格"
