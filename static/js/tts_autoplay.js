/**
 * P0-1:TTS 自动播放(WebAudio 一次性解锁方案)
 * ============================================================================
 * 背景:浏览器 autoplay policy —— 页面无用户手势前,<audio autoplay>
 * (gr.Audio autoplay=True)与 AudioContext 一律被静音拦截。本模块:
 *
 *   1. 首个用户手势(pointerdown / keydown)一次性 resume() AudioContext 解锁;
 *      之后所有 TTS 播报全自动,无需再点。
 *   2. 每 500ms 轮询 `${baseUrl}/api/tts_state`,(path, mtime) 变化且
 *      should_play=true(pure_sim)时 fetch /api/tts_audio →
 *      AudioContext.decodeAudioData → BufferSource 播放。
 *   3. real_plus_sim 模式 should_play=false —— TTS 已路由真机扬声器,
 *      浏览器不得再播(防双重发声)。
 *
 * 状态徽章(pill):🔇 未解锁 → 🔊 已解锁待命 → ▶ 播放中。
 * 文字+图标双语义(不只靠颜色),WCAG 2.1 AA。
 *
 * 留给 P0-2 的 hook:window.__reachyTtsBusyUntil = 播放结束的 epoch ms。
 * 语音免提模式(sim)下浏览器外放会被 voice_mic 采到形成声学反馈,
 * 后续 VAD/分句可参考此时间戳做播放期抑制(类比真机侧的 mic 静音)。
 *
 * 挂载方式:Gradio js_on_load 动态 import 本模块 →
 *   window.ReachyTtsAutoplay.mount(element, { baseUrl: 'http://localhost:7861' })
 * 防重复挂载:Gradio 重渲染/多次 js_on_load 只生效一次。
 */

const POLL_INTERVAL_MS = 500;
const PILL_ID = "reachy-tts-autoplay-pill";
const VOLUME_KEY = "reachy.ttsVolume"; // localStorage,0-200(100 = 原始增益)

/** @type {AudioContext | null} */
let audioCtx = null;
/** @type {GainNode | null} 播放链 source → gain → destination,滑条实时调 */
let gainNode = null;
/** 音量百分比(0-200)。Edge TTS 原始 wav 响度偏小,允许放到 2x(实测反馈)。 */
let volumePct = 100;
let unlocked = false;
let mounted = false;
let pollTimer = null;
/** @type {AudioBufferSourceNode | null} 当前正在播的 source(用于重叠时掐断) */
let currentSource = null;
/** 最近一次已处理(或已作为基线记录)的 (path|mtime) key */
let lastKey = null;
let pillEl = null;

function setPill(text, tone) {
  if (!pillEl) return;
  pillEl.textContent = text;
  pillEl.dataset.tone = tone; // locked | ready | playing | warn
}

/** 音量条初始化:读 localStorage,绑 input 事件实时调 GainNode。 */
function initVolumeSlider(root) {
  const slider = root.querySelector("#reachy-tts-volume");
  const label = root.querySelector("#reachy-tts-volume-val");
  const saved = parseInt(localStorage.getItem(VOLUME_KEY) || "100", 10);
  volumePct = Number.isFinite(saved) ? Math.min(200, Math.max(0, saved)) : 100;
  if (slider) slider.value = String(volumePct);
  if (label) label.textContent = `${volumePct}%`;
  if (gainNode) gainNode.gain.value = volumePct / 100;
  if (slider) {
    slider.addEventListener("input", (e) => {
      volumePct = parseInt(e.target.value, 10) || 0;
      localStorage.setItem(VOLUME_KEY, String(volumePct));
      if (label) label.textContent = `${volumePct}%`;
      if (gainNode) gainNode.gain.value = volumePct / 100;
    });
  }
}

/** 首次用户手势:创建/恢复 AudioContext。必须在手势回调里执行 resume()。 */
async function unlock() {
  if (unlocked) return;
  try {
    if (!audioCtx) {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      audioCtx = new Ctor();
      gainNode = audioCtx.createGain();
      gainNode.gain.value = volumePct / 100;
      gainNode.connect(audioCtx.destination);
    }
    if (audioCtx.state === "suspended") {
      await audioCtx.resume();
    }
    if (audioCtx.state === "running") {
      unlocked = true;
      document.removeEventListener("pointerdown", unlock);
      document.removeEventListener("keydown", unlock);
      setPill("🔊 语音自动播放已启用", "ready");
      console.info("[reachy-tts] AudioContext 已解锁");
    }
  } catch (e) {
    console.warn("[reachy-tts] 解锁失败(下次手势重试):", e);
  }
}

/** 播放 wav 字节;重叠时掐断旧音频(新回复优先)。 */
async function playBytes(arrayBuffer) {
  try {
    const buf = await audioCtx.decodeAudioData(arrayBuffer);
    if (currentSource) {
      try { currentSource.stop(); } catch (_) { /* 已结束 */ }
    }
    const src = audioCtx.createBufferSource();
    src.buffer = buf;
    src.connect(gainNode || audioCtx.destination);
    currentSource = src;
    window.__reachyTtsBusyUntil = Date.now() + buf.duration * 1000;
    setPill("▶ 正在播报…", "playing");
    src.onended = () => {
      if (currentSource === src) {
        currentSource = null;
        setPill("🔊 语音自动播放已启用", "ready");
      }
    };
    src.start();
  } catch (e) {
    console.error("[reachy-tts] 解码/播放失败:", e);
    setPill("⚠️ 音频解码失败(详见 console)", "warn");
  }
}

async function pollOnce(baseUrl) {
  let state;
  try {
    const r = await fetch(`${baseUrl}/api/tts_state`, { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state = await r.json();
  } catch (e) {
    setPill("⚠️ TTS 服务(7861)不可达,重试中…", "warn");
    return;
  }
  if (!state.available) {
    if (!unlocked) setPill("🔇 点击页面任意处,启用语音自动播放", "locked");
    return;
  }
  const key = `${state.path}|${state.mtime}`;
  if (lastKey === null) {
    // 基线:页面加载前已存在的音频不自动播(避免刷新页面就重播旧回复)
    lastKey = key;
    return;
  }
  if (key === lastKey) return;
  lastKey = key;
  if (!state.should_play) return; // real 模式:真机扬声器已播
  if (!unlocked || !audioCtx) {
    setPill("🔇 有新语音待播 — 点击页面任意处启用自动播放", "locked");
    return;
  }
  try {
    const r = await fetch(`${baseUrl}/api/tts_audio`, { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    await playBytes(await r.arrayBuffer());
  } catch (e) {
    console.error("[reachy-tts] 拉取音频失败:", e);
    setPill("⚠️ 拉取语音失败(详见 console)", "warn");
  }
}

/**
 * @param {HTMLElement} root gr.HTML 组件根 DOM(js_on_load 传入的 element)
 * @param {{ baseUrl: string }} opts
 */
export function mount(root, opts) {
  const { baseUrl } = opts;
  pillEl = root.querySelector(`#${PILL_ID}`);
  if (!pillEl) {
    // 调用方 HTML 没带 pill 容器时自建一个(防御)
    pillEl = document.createElement("div");
    pillEl.id = PILL_ID;
    root.appendChild(pillEl);
  }
  if (mounted) {
    // Gradio 重渲染导致 js_on_load 再触发:只重绑 pill/滑条引用,不起第二个轮询
    initVolumeSlider(root);
    return;
  }
  mounted = true;
  initVolumeSlider(root);
  setPill("🔇 点击页面任意处,启用语音自动播放", "locked");
  document.addEventListener("pointerdown", unlock);
  document.addEventListener("keydown", unlock);
  pollOnce(baseUrl); // 立即一次建立基线
  pollTimer = setInterval(() => pollOnce(baseUrl), POLL_INTERVAL_MS);
}

/** 测试/调试用手动复位(正常流程不调用)。 */
export function _resetForTest() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
  mounted = false;
  unlocked = false;
  lastKey = null;
  audioCtx = null;
  gainNode = null;
  currentSource = null;
  pillEl = null;
}

window.ReachyTtsAutoplay = { mount, _resetForTest };
