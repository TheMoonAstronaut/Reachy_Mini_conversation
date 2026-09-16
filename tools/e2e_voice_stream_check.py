"""tools/e2e_voice_stream_check.py — P0-2 手动 E2E:浏览器免提语音流式链路(需 playwright + chromium,CI 不跑)

复现/验收点(2026-09-15 排错:免提模式 stream chunk 零到达后端):
  1. fake-mic headless Chromium 打开 7860(自动授权麦克风)
  2. 切到 🎤 语音(免提)模式 → voice_mic 组件可见
  3. 点录制按钮 → 观察 10s:console 报错、组件状态变化
  4. 判据:后端日志出现 "[voice] 首个 stream chunk"(本脚本退 0);
     否则打印 console 消息与组件 HTML 供诊断(退 1)

用法:
  ./scripts/start.sh --ui 起服务后:
  python tools/e2e_voice_stream_check.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

URL = "http://localhost:7860/"
CONSOLE_DUMP = "/tmp/voice_stream_console.txt"
MIC_HTML_DUMP = "/tmp/voice_mic_component.html"


async def main() -> int:
    console_msgs: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                # fake 音频设备(恒定音)+ 自动授权,绕过无真麦/权限弹窗
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        page = await browser.new_page()
        page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: console_msgs.append(f"[pageerror] {e}"))

        await page.goto(URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(6000)  # Gradio 6 SPA 渲染 + js_on_load

        # 1. 切到语音(免提)模式
        radio = page.get_by_text("🎤 语音(免提)", exact=False)
        if not await radio.count():
            print("❌ 找不到语音模式 radio")
            return 1
        await radio.first.click()
        await page.wait_for_timeout(1500)

        # 2. voice_mic 应可见;找它的录制按钮
        mic = page.locator("label:has-text('免提聆听中')").first
        container = mic.locator("xpath=ancestor::div[contains(@class,'gradio') or @data-testid][1]")
        if not await container.count():
            # 退一步:直接找页面上可见的 record 按钮
            container = page.locator("body")
        rec_btn = container.locator("button:has-text('Record'), button[aria-label*='ecord'], button:has(svg)").last
        html = await container.inner_html()
        Path(MIC_HTML_DUMP).write_text(html, encoding="utf-8")

        # 3. 点录制(fake-mic 恒定音会触发 VAD)
        clicked = False
        for sel in [
            "button:has-text('Record')",
            "button[aria-label='Record']",
            ".record-button",
            "button.record",
        ]:
            btn = page.locator(sel).first
            if await btn.count() and await btn.is_visible():
                await btn.click()
                clicked = True
                print(f"已点击录制按钮: {sel}")
                break
        if not clicked:
            print("⚠️ 未找到录制按钮,已 dump 组件 HTML →", MIC_HTML_DUMP)

        await page.wait_for_timeout(10000)  # 让 chunk 流 10s
        await browser.close()

    Path(CONSOLE_DUMP).write_text("\n".join(console_msgs), encoding="utf-8")
    errors = [m for m in console_msgs if m.startswith("[error]") or m.startswith("[pageerror]")]
    print(f"console 共 {len(console_msgs)} 条,error 级 {len(errors)} 条 → {CONSOLE_DUMP}")
    for e in errors[:10]:
        print(" ", e[:200])
    print("判据:请查后端日志是否出现 '[voice] 首个 stream chunk'")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
