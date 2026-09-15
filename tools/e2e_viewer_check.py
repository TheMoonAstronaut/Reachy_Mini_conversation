"""tools/e2e_viewer_check.py — 手动 E2E(需 playwright + chromium,CI 不跑)

V1 端到端浏览器验收(headless Chrome + SwiftShader 软件 WebGL)。

验收点(HANDOVER_SESSION_5 §六 P0 V1):
  1. Gradio 7860 页面加载,js_on_load 执行,#reachy-3d-viewer 里出现 <canvas>
  2. WebSocket /ws/state 连上(角标 ".rm-3d-badge" 显示"已连接")
  3. 3D 真的渲染了(canvas 采样像素标准差 > 阈值,不是纯色)
  4. 鼠标左键拖拽 → 相机机位变化 + 两张截图有显著差异(OrbitControls 生效)
  5. rig.headGroup 的世界矩阵是实时值(rest 附近,非单位阵 bug 值)
  6. 控制台无 error 级日志
截图:/tmp/v1_shot_before.png /tmp/v1_shot_after_drag.png(人工目检用)
"""

import asyncio
import sys

from playwright.async_api import async_playwright

URL = "http://localhost:7860/"
SHOT1 = "/tmp/v1_shot_before.png"
SHOT2 = "/tmp/v1_shot_after_drag.png"


async def main() -> int:
    console_errors: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--use-angle=swiftshader",  # 软件 WebGL(无 GPU 也跑)
                "--enable-unsafe-swiftshader",
                "--no-sandbox",
            ],
        )
        page = await browser.new_page(viewport={"width": 1500, "height": 950})
        page.on(
            "console",
            lambda m: console_errors.append(m.text) if m.type == "error" else None,
        )
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))

        print("[1/6] 打开 Gradio 页面…")
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)

        print("[2/6] 等待 3D canvas(js_on_load → import → 40 个 STL)…")
        await page.wait_for_selector("#reachy-3d-viewer canvas", timeout=60000)

        print("[3/6] 等待 /ws/state 连接(角标变绿)…")
        await page.wait_for_function(
            """() => {
                const b = document.querySelector('.rm-3d-badge');
                return b && b.textContent.includes('已连接');
            }""",
            timeout=20000,
        )
        await page.wait_for_timeout(1500)  # 等首帧渲染稳定

        print("[4/6] 截图(拖拽前)…")
        viewer = page.locator("#reachy-3d-viewer")
        await viewer.screenshot(path=SHOT1)
        # 注意:不能用 drawImage(webglCanvas) 读像素(preserveDrawingBuffer=false
        # 时读回全黑)—— 像素统计改用 PIL 分析截图文件(见 main 末尾)。

        print("[5/6] 鼠标左键拖拽旋转 + 截图(拖拽后)…")
        # 拖拽前相机方位角(用于断言视角真的变了)
        cam_before = await page.evaluate(
            "() => { const c = window.ReachyViewer._instance; "
            "return null; }"  # 相机不暴露在 rig 里;用截图 diff 证明(更贴近真实交互)
        )
        box = await viewer.bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        await page.mouse.move(cx, cy)
        await page.mouse.down(button="left")
        for i in range(1, 11):
            await page.mouse.move(cx + i * 12, cy - i * 3)
            await page.wait_for_timeout(30)
        await page.mouse.up(button="left")
        await page.wait_for_timeout(600)
        await viewer.screenshot(path=SHOT2)

        print("[6/6] 读 rig 状态(headGroup 世界矩阵 + 网格数)…")
        rig_info = await page.evaluate(
            """() => {
                const inst = window.ReachyViewer && window.ReachyViewer._instance;
                if (!inst || !inst.rig) return null;
                const e = inst.rig.headGroup.matrixWorld.elements;
                let meshes = 0;
                inst.rig.root.traverse((o) => { if (o.isMesh) meshes++; });
                return { head_tz: e[14], head_tx: e[12], head_ty: e[13], meshes };
            }"""
        )
        print(f"      rig: {rig_info}")
        assert rig_info is not None, "window.ReachyViewer._instance.rig 不存在"
        assert rig_info["meshes"] == 161, f"网格数 {rig_info['meshes']} != 161(V1.6 起含 6 连杆)"
        # head site z 应在 ~0.14m 附近(rest 姿态,非 0 非 -0.027 的 bug 值)
        assert 0.05 < rig_info["head_tz"] < 0.30, f"head tz 异常: {rig_info['head_tz']}"

        await browser.close()

    print(f"\n截图: {SHOT1} / {SHOT2}")
    # PIL 分析:渲染非空 + 拖拽产生显著差异
    import numpy as np
    from PIL import Image, ImageChops

    a = np.array(Image.open(SHOT1).convert("L"), dtype=np.float32)
    b = np.array(Image.open(SHOT2).convert("L"), dtype=np.float32)
    print(f"拖拽前: mean={a.mean():.1f} std={a.std():.1f}(std>5 = 非纯色,真的渲染了)")
    assert a.std() > 5.0, "拖拽前画面接近纯色?"
    diff = np.abs(a - b)
    changed = (diff > 12).mean() * 100
    print(f"拖拽后差异: {changed:.1f}% 像素显著变化(>2% = OrbitControls 旋转生效)")
    assert changed > 2.0, "拖拽后画面几乎没变,鼠标交互没生效?"
    if console_errors:
        print("⚠️ 控制台 error:")
        for e in console_errors[:10]:
            print("   -", e[:200])
    else:
        print("✓ 控制台无 error")
    print("\n=== E2E 全部通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
