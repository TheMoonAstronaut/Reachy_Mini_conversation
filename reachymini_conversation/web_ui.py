"""web_ui — Gradio Blocks UI(B1 语音闭环 + B2 深色科技风重设计)。

历史:
  P1(已弃):debug 视图(头部 RPY JSON)
  P2(已弃):左 Mujoco 视频 + 右 chat 占位 + 真机摄像头占位
  P3(已弃):默认 Soft 主题 + 大段 Markdown
  P4/B1(现在):
    - 语音全链路闭环:浏览器麦克风 → PCM 16k → 豆包 ASR → 豆包 LLM → Edge TTS → 浏览器播报
    - Chatbot 支持 🎤 语音轮次 + 文字轮次,LLM 回复自动 TTS 播报(gr.Audio autoplay)
  B2(现在):
    - 深色科技风 + Reachy 品牌橙(#FF8C00)点缀
    - 顶部 pill 状态栏:系统状态 / 模式 / 声源 / 手部跟随(图标+文字+语义色)
    - 左(scale 3):MuJoCo 场景第三人称(主,studio_close→/scene_feed)
      + 机器人视角(副:sim=眼睛相机 /sim_feed,real=真机 /camera_feed)
    - 右(scale 2):对话面板(Chatbot ~420)+ 输入 + 🎤 麦克风 + TTS 播放器
    - 手部跟随 / 设置 / 工具轨迹 / 开发者调试收进 Accordion
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import gradio as gr
import numpy as np

# config.py 在根目录(不是子包),需要把根目录加 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config as _root_config  # noqa: E402

# P4:接真 LLM + TTS
from reachymini_conversation.brain.doubao_brain import DoubaoBrain  # noqa: E402

# 包内部
from reachymini_conversation.state_bus import get_state_bus  # noqa: E402
from reachymini_conversation.tts.edge_tts import EdgeTTS  # noqa: E402
from reachymini_conversation.utils.audio_convert import numpy_to_pcm16_bytes  # noqa: E402
from reachymini_conversation.utils.env_loader import (  # noqa: E402
    DEFAULT_ENV,
    get_env_json_path,
    load_env,
    save_env,
    validate_env,
)
from reachymini_conversation.voice_pipeline import VoicePipeline  # noqa: E402

logger = logging.getLogger(__name__)


# 全局 LLM + TTS + Pipeline 实例(单例)
_BRAIN_SINGLETON: DoubaoBrain | None = None
_TTS_SINGLETON: EdgeTTS | None = None
_PIPELINE_SINGLETON: VoicePipeline | None = None
_TOOL_DEPS_GLOBAL: Any = None  # app.py 注入
_ORCH_GLOBAL: Any = None  # app.py 注入(MirrorOrchestrator,V2 音频路由/真机语音用)


def get_brain() -> DoubaoBrain:
    global _BRAIN_SINGLETON
    if _BRAIN_SINGLETON is None:
        _BRAIN_SINGLETON = DoubaoBrain()
    return _BRAIN_SINGLETON


def get_tts() -> EdgeTTS:
    global _TTS_SINGLETON
    if _TTS_SINGLETON is None:
        _TTS_SINGLETON = EdgeTTS()
    return _TTS_SINGLETON


def set_orchestrator(orch: Any) -> None:
    """app.py 启动时注入 MirrorOrchestrator(V2:TTS 音频路由 + 真机语音)。"""
    global _ORCH_GLOBAL
    _ORCH_GLOBAL = orch


_SOUND_LOCALIZER_GLOBAL: Any = None


def set_sound_localizer(sl: Any) -> None:
    """app.py 启动时注入 SoundLocalizer(V2 Fix A:UI 启停声源跟随)。"""
    global _SOUND_LOCALIZER_GLOBAL
    _SOUND_LOCALIZER_GLOBAL = sl


def _get_orchestrator() -> Any:
    return _ORCH_GLOBAL


def get_pipeline() -> VoicePipeline:
    """懒创建全局 VoicePipeline(brain/tts 复用单例,ASR 每轮由工厂新建)。

    V2:push_audio_fn 接 TTS 路由 —— real 模式推真机扬声器,sim 推 sim 侧
    (浏览器播报不变,机器人侧同步有声音/wobbling 联动)。
    """
    global _PIPELINE_SINGLETON
    if _PIPELINE_SINGLETON is None:
        from reachymini_conversation.real_voice import make_tts_audio_router

        _PIPELINE_SINGLETON = VoicePipeline(
            brain=get_brain(),
            tts=get_tts(),
            push_audio_fn=make_tts_audio_router(_get_orchestrator),
        )
    return _PIPELINE_SINGLETON


def set_tool_deps(deps: Any) -> None:
    """app.py 启动时注入 ToolDependencies。"""
    global _TOOL_DEPS_GLOBAL
    _TOOL_DEPS_GLOBAL = deps


def get_tool_deps_global() -> Any:
    """返回 app.py 注入的 ToolDependencies(未注入返回 None)。

    供 app.py 在构造 RealVoiceLoop 时给共享 pipeline 注入 tool_deps ——
    2026-09-16 bug:real 语音路径漏注入,LLM 无工具可调,只能把
    "调用 dance 工具"当文本念出来(伪调用,机器人不动)。
    """
    return _TOOL_DEPS_GLOBAL


def _get_reachy_mini_for_deps() -> Any:
    """兜底:没注入 deps 时,造一个空 reachy_mini(工具调用会失败但 LLM 仍能回复)。

    app.py 启动时如果 reachy_mini 不可用,这里也不会崩。
    """
    try:
        from reachy_mini import ReachyMini

        return MagicMock(spec=ReachyMini)  # placeholder
    except Exception:
        return MagicMock()


# imports for the fallback helper
from unittest.mock import MagicMock  # noqa: E402

# ============================================================================
# B2 主题:深色科技风 + Reachy 品牌橙(#FF8C00)
# ============================================================================
# 自定义品牌橙色阶(以 #FF8C00 为 500 主色)
_REACHY_ORANGE = gr.themes.utils.colors.Color(
    name="reachy_orange",
    c50="#FFF6EB",
    c100="#FFE9CC",
    c200="#FFD199",
    c300="#FFB866",
    c400="#FFA033",
    c500="#FF8C00",  # 品牌主色
    c600="#E67E00",
    c700="#B36600",
    c800="#804B00",
    c900="#593500",
    c950="#402600",
)


def _build_theme() -> gr.themes.Base:
    """深色科技风主题:slate 中性色 + Reachy 橙点缀,直角小圆角,无浮夸阴影。"""
    return gr.themes.Base(
        primary_hue=_REACHY_ORANGE,
        secondary_hue=gr.themes.colors.slate,
        neutral_hue=gr.themes.colors.slate,
        radius_size="sm",  # 小圆角,避免"AI 味"过度圆润
        text_size="md",
        font=[
            "Inter",
            "PingFang SC",
            "Microsoft YaHei",
            "system-ui",
            "sans-serif",
        ],
        font_mono=["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
    ).set(
        # —— 全局深色底(明/暗两套都锁成同一深色,保证观感一致)——
        body_background_fill="#0D1117",
        body_background_fill_dark="#0D1117",
        body_text_color="#D5DEE8",
        body_text_color_dark="#D5DEE8",
        # —— 面板 / 块 ——
        block_background_fill="#151B24",
        block_background_fill_dark="#151B24",
        block_border_color="#2A3442",
        block_border_color_dark="#2A3442",
        block_label_text_color="#8B98AB",
        block_label_text_color_dark="#8B98AB",
        block_title_text_color="#E6EDF3",
        block_title_text_color_dark="#E6EDF3",
        block_shadow="none",
        block_shadow_dark="none",
        # —— 输入框 ——
        input_background_fill="#0F141C",
        input_background_fill_dark="#0F141C",
        input_border_color="#2A3442",
        input_border_color_dark="#2A3442",
        input_border_color_focus="#FF8C00",
        input_border_color_focus_dark="#FF8C00",
        # —— 主按钮:品牌橙 ——
        button_primary_background_fill="#FF8C00",
        button_primary_background_fill_dark="#FF8C00",
        button_primary_background_fill_hover="#FFA033",
        button_primary_background_fill_hover_dark="#FFA033",
        button_primary_text_color="#1A1206",
        button_primary_text_color_dark="#1A1206",
        # —— 强调色(color_accent 无 _dark 变体)——
        color_accent="#FF8C00",
        border_color_accent="#FF8C00",
        border_color_accent_dark="#FF8C00",
        # —— 去掉默认阴影(AI 味);shadow_drop 系列无 _dark 变体 ——
        shadow_drop="none",
        shadow_drop_lg="none",
        shadow_spread="none",
        shadow_spread_dark="none",
    )


# 模块级主题单例:Gradio 6.0 起 theme/css 从 Blocks 构造器移到 launch(),
# 这里构建一次供 app.py 的 launch() 使用(Blocks() 里再传会触发 deprecation 警告)
REACHY_THEME = _build_theme()


# 自定义 CSS:pill 徽章 / 顶栏 / 区块标题 / footer
# 与 REACHY_THEME 一样,由 app.py 的 launch(css=...) 注入(Gradio 6.0 位置)
REACHY_CSS = """
/* ===== Reachy 深色科技风(B2)===== */
.gradio-container {
  max-width: 1560px !important;
  margin: 0 auto;
}

/* ---------- 顶部状态栏 ---------- */
.rm-statusbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  margin-bottom: 10px;
  background: #11161F;
  border: 1px solid #232C3B;
  border-radius: 8px;
  flex-wrap: wrap;         /* 空间不足时徽章换行,不再溢出截断 */
  row-gap: 6px;
}
.rm-statusbar > .gradio-html {
  min-width: 0 !important; /* 允许 pill 收缩,配合 wrap 防挤压 */
}
/* Gradio Row 默认给所有子项 flex:1 1 0%(均分宽度),徽章内容被挤没。
   顶栏子项改为内容自适应;首项(品牌)吃满剩余空间,把徽章推向右侧。
   注意 gr.HTML 内部容器默认 width:100%,会把 flex-basis:auto 撑成整行,
   必须显式 fit-content(2026-09-17 实测:不设置则每项独占一行)。 */
.rm-statusbar > * { flex: 0 0 auto !important; }
.rm-statusbar > div.block { width: fit-content !important; min-width: 0 !important; }
.rm-statusbar > :first-child { margin-right: auto !important; }
.rm-statusbar .auto-margin { margin-left: 0 !important; }
.rm-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 700;
  color: #E6EDF3;
  letter-spacing: 0.02em;
  margin-right: auto;  /* 把徽章挤到右侧 */
  white-space: nowrap;
}
.rm-brand-mark {
  width: 12px; height: 12px;
  background: #FF8C00;
  border-radius: 3px;
  display: inline-block;
}
.rm-brand-sub { color: #8B98AB; font-weight: 500; font-size: 13px; }

/* ---------- pill 徽章(图标 + 文字 + 语义色,不只靠颜色) ---------- */
.pill-cell { padding: 0 !important; background: transparent !important; border: none !important; }
.pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.5;
  border: 1px solid #2A3442;
  background: #151B24;
  color: #9AA7B8;
  white-space: nowrap;
}
.pill code { background: none; border: none; color: inherit; font-size: 11.5px; padding: 0; }
.pill--ok     { color: #3FB950; border-color: rgba(63,185,80,.45);  background: rgba(63,185,80,.08); }
.pill--busy   { color: #58A6FF; border-color: rgba(88,166,255,.45); background: rgba(88,166,255,.08); }
.pill--accent { color: #FF8C00; border-color: rgba(255,140,0,.45);  background: rgba(255,140,0,.08); }
.pill--play   { color: #39C5CF; border-color: rgba(57,197,207,.45); background: rgba(57,197,207,.08); }
.pill--err    { color: #F85149; border-color: rgba(248,81,73,.45);  background: rgba(248,81,73,.10); }
.pill--muted  { color: #8B98AB; }

/* ---------- 区块标题(小号大写,科技感) ---------- */
.rm-section-title {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.10em;
  text-transform: uppercase;
  color: #8B98AB;
  border-left: 3px solid #FF8C00;
  padding-left: 8px;
  margin: 6px 0 8px 0;
}

/* ---------- 面板与组件 ---------- */
.rm-pane { gap: 10px; }
.rm-hint { font-size: 12px; color: #8B98AB; }
.rm-chatbot { border: 1px solid #2A3442; border-radius: 8px; }
.rm-mic, .rm-tts { border: 1px solid #2A3442; border-radius: 8px; }

/* ---------- 运行模式下拉(V2,紧凑嵌进状态栏) ---------- */
.rm-mode-dropdown {
  min-width: 160px !important;
  max-width: 175px;
}
.rm-mode-dropdown .wrap, .rm-mode-dropdown .wrap-inner {
  background: #151B24 !important;
  border: 1px solid #2A3442 !important;
  border-radius: 999px !important;
  min-height: 30px !important;
}
.rm-mode-dropdown input, .rm-mode-dropdown .single-select {
  color: #D5DEE8 !important;
  font-size: 12.5px !important;
}

/* ---------- 精简 footer ---------- */
.rm-footer {
  margin-top: 12px;
  padding-top: 8px;
  border-top: 1px solid #232C3B;
  font-size: 12px;
  color: #5B6779;
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.rm-footer code { color: #8B98AB; }

/* ============================================================
   文字对比度治理(2026-09-18 用户实测"深底黑字 / 白底白字")
   ------------------------------------------------------------
   背景:REACHY_THEME 的部分变量在 Gradio 6 不落(body_text_color、
   按钮面色等),未覆盖处回落到浏览器默认(黑字/白按钮),与深色面板
   混排就出现两类对比度事故。主题变量名随 Gradio 版本漂移不可靠,
   这里对用到的每种组件显式锁色:深色面 + 浅色字,语义色按钮保留。
   ============================================================ */
/* —— 全局基色:所有未单独设色的文本,深底浅字 —— */
.gradio-container, .gradio-container .main, .gradio-container .wrap,
.gradio-container .column, .gradio-container .row,
.gradio-container .prose, .gradio-container .prose p,
.gradio-container .prose li, .gradio-container .prose strong {
  color: #D5DEE8;
}
/* 次级说明文字 */
.gradio-container .prose em, .gradio-container em { color: #8B98AB; }
/* inline code:深底灰字,防"白底白字" */
.gradio-container code {
  background: #1B2432;
  color: #8B98AB;
  border: 1px solid #2A3442;
  border-radius: 4px;
  padding: 0 4px;
}

/* —— 按钮:Gradio 默认浅色面 → 锁深色面浅字;语义色按钮保留色相 —— */
.gradio-container button {
  color: #E6EDF3;
}
.gradio-container button.secondary {
  background: #1B2432 !important;
  border: 1px solid #2A3442 !important;
  color: #D5DEE8 !important;
}
.gradio-container button.primary {
  background: #D97706 !important;   /* 品牌橙(暗一档,白字可读) */
  border: 1px solid #FF8C00 !important;
  color: #FFFFFF !important;
}
.gradio-container button.stop {
  background: #6E2C2C !important;   /* 深红面,不用刺眼亮红 */
  border: 1px solid #F85149 !important;
  color: #FFB3AE !important;
}

/* —— 下拉:内部 reference/arrow 等回落黑字 → 锁浅字 —— */
.gradio-container .gradio-dropdown, .gradio-container .gradio-dropdown * {
  color: #D5DEE8;
}
.gradio-container .gradio-dropdown .wrap, .gradio-container .gradio-dropdown .wrap-inner {
  background: #151B24 !important;
  border-color: #2A3442 !important;
}

/* —— 文本框:标签 + 输入 + 占位 —— */
.gradio-container .gradio-textbox label span,
.gradio-container .gradio-textbox .label-wrap span {
  color: #8B98AB;
}
.gradio-container .gradio-textbox input,
.gradio-container .gradio-textbox textarea {
  background: #0F141C !important;
  color: #E6EDF3 !important;
  border-color: #2A3442 !important;
}
.gradio-container .gradio-textbox input::placeholder,
.gradio-container .gradio-textbox textarea::placeholder {
  color: #5B6779;
}

/* —— Gradio 6 组件块标签 chip(对话历史/语音输入/Reachy 语音播报等):
   默认白底 chip 是"白底白字/浅底深字"事故重灾区 → 统一透明底灰字 —— */
.gradio-container [data-testid="block-label"] {
  background: transparent !important;
  color: #8B98AB !important;
  border: none !important;
  box-shadow: none !important;
}
.gradio-container [data-testid="block-label"] span {
  color: #8B98AB !important;
}

/* —— 单选(对话模式,Gradio 6 结构 fieldset.rm-chat-mode .wrap label):
   默认白丸 → 深面浅字,选中项橙色描边 —— */
.rm-chat-mode .wrap {
  background: transparent !important;
}
.rm-chat-mode label[data-testid$="-radio-label"] {
  background: #151B24 !important;
  color: #D5DEE8 !important;
  border: 1px solid #2A3442 !important;
}
.rm-chat-mode label.selected[data-testid$="-radio-label"] {
  background: #1B2432 !important;
  color: #FF8C00 !important;
  border-color: #FF8C00 !important;
}
.rm-chat-mode label[data-testid$="-radio-label"] span {
  color: inherit !important;
}

/* —— 文本框发送按钮:默认白圆 → 深色面橙字 —— */
.gradio-container .submit-button {
  background: #1B2432 !important;
  color: #FF8C00 !important;
  border: 1px solid #2A3442 !important;
}

/* —— 聊天窗:占位 / 气泡(Gradio 6 结构:.message-row.bubble
   .message.bot|.user;气泡默认近白底 rgb(248,250,252),叠全局浅色字
   规则 = 白底白字事故)→ 深底浅字 —— */
/* (block-label 全局规则见上方单选区块) */
.gradio-container .placeholder {
  color: #5B6779 !important;
}
.gradio-container .message-row .message.bot {
  background: #151B24 !important;
  color: #D5DEE8 !important;
  border: 1px solid #2A3442 !important;
}
.gradio-container .message-row .message.user {
  background: #1B2432 !important;
  color: #E6EDF3 !important;
  border: 1px solid #2A3442 !important;
}
/* 气泡内 markdown 继承气泡色(防上方全局 .prose 浅色字规则串色) */
.gradio-container .message-row .message.bot .md,
.gradio-container .message-row .message.user .md,
.gradio-container .message-row .message.bot .prose,
.gradio-container .message-row .message.user .prose {
  color: inherit !important;
}

/* —— 音频组件(录音/TTS/免提):浅面板 → 深面 —— */
.gradio-container .rm-mic, .gradio-container .rm-tts {
  background: #0F141C !important;
}
.gradio-container .rm-mic .label-wrap span,
.gradio-container .rm-tts .label-wrap span,
.gradio-container .rm-mic label,
.gradio-container .rm-tts label {
  color: #8B98AB !important;
}

/* —— Accordion 标签 —— */
.gradio-container .gradio-accordion > .label-wrap span,
.gradio-container .gradio-accordion .label span {
  color: #C9D4E0 !important;
}

/* —— JSON(工具轨迹/开发者调试)—— */
.gradio-container .gradio-json {
  background: #0F141C !important;
  color: #C9D4E0 !important;
  border-color: #2A3442 !important;
}
"""

# ============================================================================
# 状态徽章(status → 图标文字 + pill 语义色 class)
# ============================================================================
STATUS_BADGE: dict[str, tuple[str, str]] = {
    "starting": ("🟡 启动中", "pill--busy"),
    "running": ("🟢 运行中", "pill--ok"),
    "stopping": ("🟠 停止中", "pill--accent"),
    "stopped": ("⚫ 已停止", "pill--muted"),
    "idle": ("⚪ 待机", "pill--muted"),
    "thinking": ("🧠 思考中…", "pill--busy"),
    "speaking": ("🗣️ Reachy 说话中", "pill--accent"),
    "playing": ("🔊 播放音频", "pill--play"),
    "listening": ("👂 聆听识别中…", "pill--ok"),
    "error": ("🔴 错误", "pill--err"),
}

# P4 状态常量(从 voice_pipeline 复用)
STATE_IDLE = "idle"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"
STATE_PLAYING = "playing"
STATE_LISTENING = "listening"
STATE_ERROR = "error"

# ============================================================================
# V2 运行模式下拉:显示文案 ↔ (run_mode, conn_cfg)
# ============================================================================
_RUN_MODE_PURE_SIM = "🧪 纯仿真"
_RUN_MODE_REAL_WIRED = "🤖 真机+仿真(有线)"
_RUN_MODE_REAL_WIRELESS = "🌐 真机+仿真(无线)"
_RUN_MODE_CHOICES = [_RUN_MODE_PURE_SIM, _RUN_MODE_REAL_WIRED, _RUN_MODE_REAL_WIRELESS]


def _run_mode_choice_to_request(choice: str) -> tuple[str, dict[str, Any]]:
    """下拉文案 → (目标 run_mode, real_connection 配置)。"""
    if choice == _RUN_MODE_REAL_WIRED:
        return "real_plus_sim", {"type": "wired", "port": 8001}
    if choice == _RUN_MODE_REAL_WIRELESS:
        return "real_plus_sim", {"type": "wireless", "host": "reachy-mini.local", "port": 8000}
    return "pure_sim", {}


def _run_mode_to_choice(run_mode: str, real_connecting: bool = False) -> str:
    if real_connecting:
        return _RUN_MODE_REAL_WIRED  # 切换中保持显示目标态(简化)
    return _RUN_MODE_PURE_SIM if run_mode == "pure_sim" else _RUN_MODE_REAL_WIRED


# ============================================================================
# 渲染辅助在文件底部(_mjpeg_img_html / _scene_feed_html / _eye_feed_html 等)
# ============================================================================


def build_ui() -> gr.Blocks:
    bus = get_state_bus()

    # Gradio 6.0:theme/css 不再放 Blocks 构造器,改由 app.py 的 launch() 传入
    # (Blocks(theme=..., css=...) 会触发 "moved to launch()" UserWarning)
    with gr.Blocks(
        title="Reachy Mini Conversation",
        analytics_enabled=False,
    ) as demo:
        # ---------- 顶部:品牌 + 4 个 pill 状态徽章 + 运行模式下拉 ----------
        with gr.Row(elem_classes=["rm-statusbar"], equal_height=False):
            gr.HTML(
                '<div class="rm-brand"><span class="rm-brand-mark"></span>'
                "Reachy&nbsp;Mini&nbsp;<span class='rm-brand-sub'>Conversation</span></div>"
            )
            status_pill = gr.HTML(value=_render_status(bus.snapshot()), elem_classes=["pill-cell"])
            mode_pill = gr.HTML(
                value=_render_run_mode(bus.get("run_mode", "pure_sim")),
                elem_classes=["pill-cell"],
            )
            doa_pill = gr.HTML(value=_render_doa(bus.snapshot()), elem_classes=["pill-cell"])
            hand_pill = gr.HTML(value=_render_hand(bus.snapshot()), elem_classes=["pill-cell"])
            # V2:运行模式下拉切换(纯仿真 ↔ 真机+仿真 有线/无线)
            mode_dropdown = gr.Dropdown(
                choices=_RUN_MODE_CHOICES,
                value=_RUN_MODE_PURE_SIM,
                show_label=False,
                container=False,
                interactive=True,
                min_width=165,
                elem_classes=["rm-mode-dropdown"],
            )
            # 真机连接/断开快捷按钮(2026-09-16 用户需求:网页先切真机模式、
            # 后插 USB 时需要反复切换才能连上;给显式重连/断开入口,
            # 复用下拉同一 switch 通路)
            connect_real_btn = gr.Button(
                "⚡ 连接真机", size="sm", min_width=96,
                elem_classes=["rm-mode-btn"],
            )
            disconnect_real_btn = gr.Button(
                "✕ 断开真机", size="sm", min_width=96,
                elem_classes=["rm-mode-btn"],
            )
        # V2:模式切换的结果提示(切真机的进度/失败原因),紧贴状态栏下方
        mode_switch_status = gr.Markdown(value="", elem_classes=["rm-hint"])

        # ---------- 主区:左视频(scale 3)+ 右对话(scale 2) ----------
        with gr.Row():
            # ---- 左:视频区 ----
            with gr.Column(scale=3, elem_classes=["rm-pane"]):
                # V1 主区:three.js 交互式 3D 视图(方案 B)
                # 数据:ws://localhost:7861/ws/state @25Hz(关节 + head_pose)
                # 资源:http://localhost:7861/static/(manifest + STL + three.js)
                # 交互:左键旋转 / 右键平移 / 滚轮缩放(OrbitControls,对齐 MuJoCo viewer)
                gr.HTML(
                    '<div class="rm-section-title">🦾 Reachy 3D · 交互视图'
                    '<span style="font-weight:400;text-transform:none;letter-spacing:0;">'
                    '(左键旋转 · 右键平移 · 滚轮缩放)</span></div>'
                )
                viewer_3d_html = gr.HTML(
                    value=_viewer_3d_html(),
                    js_on_load=_VIEWER_3D_JS_ON_LOAD,
                    min_height=490,
                )

                # 原场景 MJPEG 降级为折叠对照(真·MuJoCo 渲染画面,验证 3D 视图用)
                with gr.Accordion("🎥 MuJoCo 渲染画面(对照 · MJPEG)", open=False):
                    scene_feed_html = gr.HTML(value=_scene_feed_html(scene_available=True))
                    scene_feed_status_md = gr.Markdown(
                        value="_正在检查 `/scene_feed_status` …_",
                        elem_classes=["rm-hint"],
                    )

                # 副视角:sim 模式 = 机器人眼睛相机(/sim_feed);real 模式 = 真机摄像头(/camera_feed)
                gr.HTML(
                    '<div class="rm-section-title" id="eye-feed-title">🤖 机器人视角 · 副视角(sim=眼睛 / 真机=实拍)</div>'
                )
                eye_feed_html = gr.HTML(
                    value=_eye_feed_html(
                        run_mode=bus.get("run_mode", "pure_sim"), eye_available=True
                    )
                )
                eye_feed_status_md = gr.Markdown(
                    value="_正在检查 `/sim_feed_status` …_",
                    elem_classes=["rm-hint"],
                )

            # ---- 右:对话面板 ----
            with gr.Column(scale=2, elem_classes=["rm-pane"]):
                # V2.3:对话模式切换(文本 ↔ 语音免提)
                with gr.Row(equal_height=False):
                    gr.HTML('<div class="rm-section-title">💬 对话面板</div>')
                    chat_mode_radio = gr.Radio(
                        choices=[("💬 文本", "text"), ("🎤 语音(免提)", "voice")],
                        value="text",
                        show_label=False,
                        container=False,
                        elem_classes=["rm-chat-mode"],
                    )
                chatbot = gr.Chatbot(
                    value=[],
                    label="对话历史",
                    height=520,
                    placeholder=(
                        "和 Reachy 开始对话吧:\n"
                        "· 文字:「你好」「点点头」「跳一段舞」(LLM 会调用工具)\n"
                        "· 语音:点下方 🎤 录音,停止后自动识别并回复"
                    ),
                    elem_classes=["rm-chatbot"],
                )
                msg_input = gr.Textbox(
                    label="输入",
                    placeholder="输入文字,Enter 发送;或用下方 🎤 语音输入…",
                    submit_btn="发送",  # Enter 也提交
                )
                clear_btn = gr.Button("清空对话", variant="stop", size="sm")

                # B1:语音输入(麦克风)+ TTS 播报(浏览器自动播放)
                with gr.Row():
                    mic_input = gr.Audio(
                        sources=["microphone"],
                        type="numpy",
                        label="🎤 语音输入(停止录音后自动识别)",
                        elem_classes=["rm-mic"],
                    )
                    tts_player = gr.Audio(
                        label="🔊 Reachy 语音播报",
                        # P0-1:自动播报统一走 WebAudio(tts_autoplay.js,sim 模式
                        # should_play=True);本组件 autoplay 必须为 False —— 否则
                        # real 模式真机 push 播放的同时浏览器 <audio autoplay>
                        # 也响,同一回答播两次(2026-09-16 用户实测双播 bug)。
                        autoplay=False,
                        interactive=False,
                        elem_classes=["rm-tts"],
                    )
                # tts_player 仅作手动重播回退;自动播报通道是 WebAudio 轮询。
                gr.HTML(
                    value=_TTS_AUTOPLAY_HTML,
                    js_on_load=_TTS_AUTOPLAY_JS_ON_LOAD,
                )
                # V2.3:语音免提模式(流式麦克风,说完自动识别并回复,持续聆听)
                voice_mic = gr.Audio(
                    sources=["microphone"],
                    type="numpy",
                    streaming=True,
                    label="🎤 免提聆听中(点 ⏺ 开始;说完自动识别;再点 ⏹ 结束)",
                    visible=False,
                    elem_classes=["rm-mic"],
                )
                # real+语音模式:浏览器麦隐藏,改用真机麦克风(RealVoiceLoop)
                real_voice_hint = gr.Markdown(
                    value=(
                        "🎤 **真机语音模式**:直接对着 Reachy Mini 说话即可\n\n"
                        "_机器人自带麦克风聆听中,说完自动识别并**从真机扬声器**回答你_"
                    ),
                    visible=False,
                    elem_classes=["rm-hint"],
                )

                # P6:手部跟随启停(简单 UI 按钮,状态写到 state_bus)
                with gr.Accordion("🤚 手部跟随(P6)", open=False):
                    gr.Markdown(
                        "_MediaPipe HandLandmarker,手掌中心(中指根部)驱动头部看向。"
                        "检测到手后头部平滑跟随(EMA 防抖)。_\n\n"
                        "**动作仲裁**:Reachy 说话/播报时自动让位(wobbler 摆头优先);"
                        "跟随期间空闲呼吸自动暂停;与「声源跟随」同开会互相争抢头部,"
                        "**建议只开一个**。_\n\n"
                        "⚠️ **必须先顶栏 ⚡ 连接真机** — 跟随看的是真机 USB 相机的画面"
                        "(真手要在机器人镜头前);纯仿真模式的相机是 Mujoco 合成画面,"
                        "**永远检测不到手**。"
                    )
                    with gr.Row():
                        hand_start_btn = gr.Button("▶ 启动跟随", variant="primary")
                        hand_stop_btn = gr.Button("⏹ 停止跟随", variant="stop")
                    hand_status = gr.Markdown(value="(状态见顶部徽章)")

                    def _set_hand_requested(requested: bool) -> str:
                        from reachymini_conversation.state_bus import get_state_bus

                        get_state_bus().update("hand_follow_requested", requested)
                        return "✅ 已请求启动" if requested else "⏹ 已请求停止"

                    hand_start_btn.click(
                        lambda: _set_hand_requested(True),
                        inputs=[],
                        outputs=[hand_status],
                    )
                    hand_stop_btn.click(
                        lambda: _set_hand_requested(False),
                        inputs=[],
                        outputs=[hand_status],
                    )

                # V2 Fix A:声源跟随启停(默认关;真机接入后 ReSpeaker 活了会误触)
                with gr.Accordion("🧭 声源跟随(P5,默认关)", open=False):
                    gr.Markdown(
                        "_检测到说话声时身体自动转向声源。真机(ReSpeaker)或模拟均可用。"
                        "关闭时仅显示角度,不驱动转头。_"
                    )
                    with gr.Row():
                        doa_start_btn = gr.Button("▶ 开启跟随", variant="primary")
                        doa_stop_btn = gr.Button("⏹ 关闭跟随", variant="stop")
                    doa_status = gr.Markdown(value="(状态见顶部徽章)")

                    def _set_doa_follow(enabled: bool) -> str:
                        if _SOUND_LOCALIZER_GLOBAL is None:
                            return "⚠️ SoundLocalizer 未初始化"
                        _SOUND_LOCALIZER_GLOBAL.set_enabled(enabled)
                        return "✅ 已开启声源跟随" if enabled else "⏹ 已关闭声源跟随"

                    doa_start_btn.click(
                        lambda: _set_doa_follow(True), inputs=[], outputs=[doa_status]
                    )
                    doa_stop_btn.click(
                        lambda: _set_doa_follow(False), inputs=[], outputs=[doa_status]
                    )

                # ===== 设置面板(P3) =====
                with gr.Accordion("⚙️ 设置(API Key / Model)", open=False):
                    gr.Markdown(
                        "_编辑后点 **保存** → 写到 `~/.reachymini/env.json`(0600)。"
                        "改完需 **重启** 进程生效。_"
                    )

                    # 从 env.json 读当前值,缺则用 default
                    _cur = load_env()
                    _llm = _cur.get("doubao_llm", DEFAULT_ENV["doubao_llm"])
                    _asr = _cur.get("doubao_asr", DEFAULT_ENV["doubao_asr"])
                    _tts = _cur.get("edge_tts", DEFAULT_ENV["edge_tts"])

                    llm_api_key_input = gr.Textbox(
                        label="豆包 LLM API Key",
                        type="password",
                        value=_llm.get("api_key", ""),
                        placeholder="留空 = 不修改",
                    )
                    llm_base_url_input = gr.Textbox(
                        label="豆包 LLM Base URL",
                        value=_llm.get("base_url", DEFAULT_ENV["doubao_llm"]["base_url"]),
                    )
                    llm_model_input = gr.Textbox(
                        label="模型 ID",
                        value=_llm.get("model", DEFAULT_ENV["doubao_llm"]["model"]),
                    )
                    asr_api_key_input = gr.Textbox(
                        label="豆包 ASR API Key(语音输入🎤需要)",
                        type="password",
                        value=_asr.get("api_key", ""),
                        placeholder="留空 = 不修改",
                    )
                    edge_voice_input = gr.Textbox(
                        label="Edge TTS 声音",
                        value=_tts.get("voice", DEFAULT_ENV["edge_tts"]["voice"]),
                    )
                    save_btn = gr.Button("💾 保存到 env.json", variant="primary")
                    save_status = gr.Markdown(value="&nbsp;")  # 占位提示

                    gr.Markdown(
                        f"_env.json 路径:`{get_env_json_path()}`_\n\n"
                        "_网络白名单:`ark.cn-beijing.volces.com` / "
                        "`openspeech.bytedance.com` / `speech.platform.bing.com`_",
                        elem_classes=["rm-hint"],
                    )

        # ---------- 中下部:工具轨迹 + dev 调试(折叠) ----------
        with gr.Row():
            with gr.Column():
                # P7:工具调用轨迹(LLM 触发过的工具 + 参数 + 结果)
                with gr.Accordion("🛠️ 工具调用轨迹(P7)", open=False):
                    tool_trace_json = gr.JSON(
                        label="最近一次 LLM 调用的工具",
                        value=bus.get("last_tool_calls") or [],
                    )

                # dev 调试(折叠)
                with gr.Accordion("🔧 开发者调试(头部 RPY)", open=False):
                    with gr.Row():
                        with gr.Column():
                            gr.Markdown("**头部 Joints(度)**")
                            head_joints_json = gr.JSON(value=bus.get("head_joints"))
                        with gr.Column():
                            gr.Markdown("**Antennas(度)**")
                            antennas_json = gr.JSON(value=bus.get("antennas"))
                        with gr.Column():
                            gr.Markdown("**元数据**")
                            meta_json = gr.JSON(value=_render_meta(bus.snapshot()))

        # ---------- Footer(精简) ----------
        gr.HTML(
            '<div class="rm-footer">'
            "<span>📍 项目:<code>/home/seeed/Reachy_Mini_conversation</code></span>"
            "<span>📋 <code>agents.local.md</code></span>"
            "<span>🗺 <code>plan.md</code></span>"
            "<span>🧪 <code>pytest tests/</code></span>"
            "</div>"
        )

        # ---------- Timer 每秒刷新 ----------
        timer = gr.Timer(value=1.0, active=True)

        _tick_state = {"last_voice_turn_seq": 0}  # 已消费的语音轮次序号

        async def tick_async(history: list[dict[str, Any]]) -> dict[str, Any]:
            snap = bus.snapshot()
            run_mode = snap.get("run_mode", "pure_sim")
            # 主区:轮询 /scene_feed_status(场景流),按 availability 切换 真视频 ↔ 占位
            try:
                scene_status = await _fetch_feed_status("scene_feed_status")
            except Exception as e:
                logger.debug(f"tick: scene_status fetch failed: {e}")
                scene_status = {"available": False, "frames_received": 0, "frames_attempted": 0}
            scene_html = _scene_feed_status_html(
                available=scene_status["available"],
                frames_received=scene_status["frames_received"],
                frames_attempted=scene_status["frames_attempted"],
            )
            scene_status_md_text = (
                f"**场景视频流(studio_close)**:{'✅ 真视频' if scene_status['available'] else '⏳ 等待信号(恢复后自动切换)'} "
                f"_(已收到 {scene_status['frames_received']} 帧 / 尝试 {scene_status['frames_attempted']} 次)_"
            )
            # 副视角:sim 模式轮询 /sim_feed_status(眼睛流);real 模式直连 /camera_feed
            if run_mode == "pure_sim":
                try:
                    eye_status = await _fetch_feed_status("sim_feed_status")
                except Exception as e:
                    logger.debug(f"tick: sim_status fetch failed: {e}")
                    eye_status = {"available": False, "frames_received": 0, "frames_attempted": 0}
                eye_html = _eye_feed_html(run_mode=run_mode, eye_available=eye_status["available"])
                eye_status_md_text = (
                    f"**机器人眼睛流(eye_camera)**:{'✅ 真视频' if eye_status['available'] else '⏳ 等待信号(恢复后自动切换)'} "
                    f"_(已收到 {eye_status['frames_received']} 帧 / 尝试 {eye_status['frames_attempted']} 次)_"
                )
            else:
                eye_html = _eye_feed_html(run_mode=run_mode, eye_available=True)
                eye_status_md_text = "_real 模式:副视角直连真机摄像头 `/camera_feed`_"
            # real+voice 语音轮次回写:RealVoiceLoop 后台线程识别/回复后写 bus,
            # tick 发现新 seq 时 append 到 chatbot(用户可见"我说了什么/机器人答了什么")
            chatbot_update: Any = gr.update()
            vt_seq = snap.get("voice_turn_seq") or 0
            if vt_seq and vt_seq != _tick_state["last_voice_turn_seq"]:
                _tick_state["last_voice_turn_seq"] = vt_seq
                vt = snap.get("voice_turn") or {}
                if vt.get("user"):
                    history = history + [
                        {"role": "user", "content": f"🎤 {vt['user']}"},
                        {"role": "assistant", "content": vt.get("reply") or "(无回复)"},
                    ]
                    chatbot_update = history
            return {
                status_pill: _render_status(snap),
                mode_pill: _render_run_mode(
                    run_mode, real_connecting=bool(snap.get("real_connecting"))
                ),
                doa_pill: _render_doa(snap),
                hand_pill: _render_hand(snap),
                head_joints_json: snap.get("head_joints"),
                antennas_json: snap.get("antennas"),
                meta_json: _render_meta(snap),
                tool_trace_json: snap.get("last_tool_calls") or [],
                scene_feed_html: scene_html,
                scene_feed_status_md: scene_status_md_text,
                eye_feed_html: eye_html,
                eye_feed_status_md: eye_status_md_text,
                chatbot: chatbot_update,
            }

        timer.tick(
            tick_async,
            inputs=[chatbot],
            outputs=[
                status_pill,
                mode_pill,
                doa_pill,
                hand_pill,
                head_joints_json,
                antennas_json,
                meta_json,
                tool_trace_json,
                scene_feed_html,
                scene_feed_status_md,
                eye_feed_html,
                eye_feed_status_md,
                chatbot,
            ],
        )

        # ---------- 事件绑定 ----------
        # V2:运行模式切换(阻塞切换放后台线程,UI 不卡);下拉与连接/断开按钮复用
        async def _do_mode_switch(choice: str) -> dict:
            import asyncio

            from reachymini_conversation.mode_manager import get_mode_manager

            mm = get_mode_manager()
            if mm is None:
                return {mode_switch_status: "⚠️ ModeManager 未初始化"}
            target_mode, conn_cfg = _run_mode_choice_to_request(choice)
            bus_now = get_state_bus()
            chat_mode = bus_now.get("chat_mode", "text")

            def _vis(final_run_mode: str) -> dict:
                voice = chat_mode == "voice"
                return {
                    voice_mic: gr.update(visible=voice and final_run_mode == "pure_sim"),
                    real_voice_hint: gr.update(visible=voice and final_run_mode != "pure_sim"),
                    # 切换结果同步回下拉显示(防状态与下拉不一致)
                    mode_dropdown: gr.update(
                        value=_run_mode_to_choice(final_run_mode)
                    ),
                }

            if target_mode == mm.current_mode:
                return {mode_switch_status: f"已处于{choice}", **_vis(mm.current_mode)}
            bus_now.update("real_connecting", True)
            try:
                result = await asyncio.to_thread(mm.switch_to, target_mode, conn_cfg)
            except Exception as e:
                logger.exception("[_do_mode_switch] switch_to 异常")
                bus_now.update("real_connecting", False)
                return {
                    mode_switch_status: f"❌ 切换异常:{type(e).__name__}: {e}",
                    **_vis(mm.current_mode),
                }
            if result["ok"]:
                return {mode_switch_status: f"✅ 已切换:{choice}", **_vis(result["run_mode"])}
            return {
                mode_switch_status: f"❌ {result['error']}(已回滚纯仿真)",
                **_vis("pure_sim"),
            }

        async def on_mode_change(choice: str) -> dict:
            return await _do_mode_switch(choice)

        async def on_connect_real() -> dict:
            """显式连接真机(同下拉选"真机+仿真(有线)",USB 后插时按此重连)。"""
            return await _do_mode_switch(_RUN_MODE_REAL_WIRED)

        async def on_disconnect_real() -> dict:
            """显式断开真机(同下拉选"纯仿真")。"""
            return await _do_mode_switch(_RUN_MODE_PURE_SIM)

        mode_dropdown.change(
            on_mode_change,
            inputs=[mode_dropdown],
            outputs=[mode_switch_status, voice_mic, real_voice_hint, mode_dropdown],
        )
        connect_real_btn.click(
            on_connect_real,
            outputs=[mode_switch_status, voice_mic, real_voice_hint, mode_dropdown],
        )
        disconnect_real_btn.click(
            on_disconnect_real,
            outputs=[mode_switch_status, voice_mic, real_voice_hint, mode_dropdown],
        )

        def _build_tool_deps() -> Any:
            """构造 ToolDependencies(从 app.py 注入的全局实例)。

            web_ui 是 P0.4 写的,不知道 app.py 的实例。
            解决方案:用一个 lazy 模块级变量,app.py 启动后注入。
            """
            global _TOOL_DEPS_GLOBAL
            if _TOOL_DEPS_GLOBAL is None:
                # 没有注入 → 返回空 deps(LLM 调工具时会报 no tool_deps)
                from tools.core_tools import ToolDependencies

                _TOOL_DEPS_GLOBAL = ToolDependencies(
                    reachy_mini=_get_reachy_mini_for_deps(),
                    movement_manager=None,
                )
            return _TOOL_DEPS_GLOBAL

        async def _tts_and_play(tts: EdgeTTS, text: str) -> str | None:
            """TTS 合成,返回 wav 路径(给浏览器播报组件用)。"""
            wav = await tts.synthesize_async(text)
            if wav:
                get_state_bus().update("last_audio_path", wav)
            return wav

        # P7 Chatbot:输入文本 → 真豆包 LLM(带 tool calling)→ 工具执行 → 回复 → TTS 播报
        async def respond(
            message: str,
            history: list[dict[str, Any]],
        ) -> tuple[list[dict[str, Any]], str, Any]:
            """用户输入 → 真豆包 LLM + 工具执行(P7) + TTS 播音(B1 输出到 tts_player)。

            async 函数(Gradio 6.0 支持)→ 直接 await brain.query_async,
            避免在 AnyIO worker thread 里 asyncio.run 的事件循环冲突。
            """
            if not message or not message.strip():
                return history, "", gr.update()

            bus = get_state_bus()
            bus.update("status", STATE_THINKING)
            bus.update("error", None)
            bus.update("last_user_text", message)

            # 1. 真 LLM 查询(P7 带 tool_deps)— async,直接 await
            brain = get_brain()
            tool_deps = _build_tool_deps()
            try:
                result = await brain.query_async(message, tool_deps=tool_deps)
                reply = result.reply
                # TTS 只念 LLM 回复本体;工具轨迹拼接版(下方)仅进聊天窗展示。
                # (2026-09-16 用户实测:拼接版喂 TTS 会把 "已执行:play_emotion(
                # {'emotion': 'hello'})" 连括号引号下划线一起念出来)
                reply_plain = reply

                # 工具调用轨迹 → state_bus
                tool_trace = [
                    {
                        "name": tc.name,
                        "arguments": tc.arguments,
                        "result": tc.result,
                    }
                    for tc in result.tool_calls
                ]
                bus.update("last_tool_calls", tool_trace)

                # 在 reply 末尾追加工具调用摘要(仅展示层)
                if tool_trace:
                    summary = ", ".join(f"{tc['name']}({tc['arguments']})" for tc in tool_trace)
                    reply = f"{reply}\n_(已执行:{summary})_"
            except Exception as e:
                logger.exception("[respond] LLM 调用失败")
                reply = f"(LLM 错误:{type(e).__name__}: {e})"
                reply_plain = reply

            bus.update("status", STATE_SPEAKING if reply else STATE_IDLE)
            bus.update("last_reply", reply)

            history = history + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": reply},
            ]

            # 2. TTS(async,直接 await)→ 输出到浏览器播报组件
            #    输入用 reply_plain(无工具轨迹拼接),轨迹文本不进播报
            wav: str | None = None
            if reply_plain:
                bus.update("status", STATE_PLAYING)
                try:
                    wav = await _tts_and_play(get_tts(), reply_plain)
                except Exception as e:
                    logger.warning(f"[respond] TTS 失败: {e}")

            bus.update("status", STATE_IDLE)
            # wav 为 None 时 gr.update() = 不动播放器(保留上一段)
            return history, "", (wav if wav else gr.update())

        # B1:麦克风录音停止 → PCM 转换 → run_audio(ASR→LLM→TTS)→ Chatbot + 播报
        async def on_mic_stop(
            audio: tuple[int, np.ndarray] | None,
            history: list[dict[str, Any]],
        ) -> tuple[list[dict[str, Any]], Any]:
            """语音轮次:🎤 录音 → 豆包 ASR → LLM(带工具)→ TTS 播报。

            状态流转:listening(识别中)→ thinking → speaking/playing → idle。
            所有失败路径都落到 Chatbot 友好提示 + bus error,不 crash。
            """
            bus = get_state_bus()
            # P0-2 埋点:诊断"浏览器麦根本没把音频送来 vs 后端处理失败"。
            # 若用户点录/停后日志无此行 → 前端组件/权限层问题,音频未到后端。
            if audio is None:
                logger.info("[voice] on_mic_stop 触发但 audio=None(未录到数据)")
                return history, gr.update()
            _sr, _data = audio
            logger.info(
                "[voice] on_mic_stop 收到录音: sr=%s, samples=%s, dtype=%s",
                _sr, getattr(_data, "shape", None), getattr(_data, "dtype", None),
            )

            bus.update("status", STATE_LISTENING)

            # 1. Gradio (sample_rate, ndarray) → PCM 16-bit/16kHz/mono bytes
            sample_rate, data = audio
            try:
                pcm = numpy_to_pcm16_bytes(sample_rate, data)
            except Exception as e:
                logger.exception("[on_mic_stop] 音频格式转换失败")
                bus.update("status", STATE_ERROR)
                bus.update("error", f"audio_convert: {e}")
                history = history + [
                    {"role": "user", "content": "🎤 (语音输入)"},
                    {
                        "role": "assistant",
                        "content": f"⚠️ 音频格式转换失败({type(e).__name__}),请重试。",
                    },
                ]
                return history, gr.update()

            if not pcm:
                bus.update("status", STATE_IDLE)
                history = history + [
                    {"role": "user", "content": "🎤 (语音输入)"},
                    {"role": "assistant", "content": "🎤 没有录到声音,请再试一次。"},
                ]
                return history, gr.update()

            # P0-2 诊断:pcm 响度(rms)+ 存盘回放。豆包服务端判"无语音"会
            # 1s 秒关连接(2026-09-15 实测)——rms 接近 0 即确认录到静音,
            # /tmp/last_mic_input.wav 可 ffplay 回放验证采到的是哪个设备。
            _arr = np.frombuffer(pcm, dtype="<i2")
            _rms = float(np.sqrt(np.mean(_arr.astype("float32") ** 2))) if _arr.size else 0.0
            logger.info(
                "[voice] pcm 就绪: %d bytes (%.1fs @16k), rms=%.1f %s",
                len(pcm), len(pcm) / 2 / 16000, _rms,
                "⚠️ 近静音!" if _rms < 100 else "",
            )
            try:
                import wave as _wave

                with _wave.open("/tmp/last_mic_input.wav", "wb") as _w:
                    _w.setnchannels(1)
                    _w.setsampwidth(2)
                    _w.setframerate(16000)
                    _w.writeframes(pcm)
                logger.info("[voice] 调试录音已存 /tmp/last_mic_input.wav")
            except Exception as _e:
                logger.warning("[voice] 调试录音存盘失败: %s", _e)

            # 2. run_audio:ASR → LLM → TTS(pipeline 内部已 try/except + bus 状态流转)
            pipeline = get_pipeline()
            pipeline.tool_deps = _build_tool_deps()  # app.py 注入的真 deps(工具动作)
            try:
                result = await pipeline.run_audio(pcm)
            except Exception as e:
                # 双保险:run_audio 已捕获 ASR 异常,这里兜底 LLM 等意外异常
                logger.exception("[on_mic_stop] pipeline.run_audio 意外失败")
                bus.update("status", STATE_ERROR)
                bus.update("error", f"voice_pipeline: {e}")
                history = history + [
                    {"role": "user", "content": "🎤 (语音输入)"},
                    {
                        "role": "assistant",
                        "content": f"⚠️ 语音管线出错({type(e).__name__}: {e}),请重试。",
                    },
                ]
                return history, gr.update()

            # 3. ASR 失败 / 空识别 → 友好提示(不崩)
            if result.error:
                err = result.error
                if "api_key" in err.lower() or "未配置" in err:
                    tip = (
                        "🎤 语音识别不可用:**ASR API Key 未配置**。\n\n"
                        "请在下方「⚙️ 设置」里填写豆包 ASR API Key,保存并重启后再试。"
                    )
                elif "未识别到" in err:
                    tip = "🎤 没听清(未识别到有效语音),请靠近麦克风再说一次。"
                else:
                    tip = f"🎤 语音识别失败:{err}"
                history = history + [
                    {"role": "user", "content": "🎤 (语音输入)"},
                    {"role": "assistant", "content": tip},
                ]
                return history, gr.update()

            # 4. 正常轮次:🎤 标记识别出的文本 + 助手回复 + TTS 播报
            history = history + [
                {"role": "user", "content": f"🎤 {result.user_text}"},
                {"role": "assistant", "content": result.reply_text or "(无回复)"},
            ]
            return history, (result.audio_path if result.audio_path else gr.update())

        msg_input.submit(
            respond,
            inputs=[msg_input, chatbot],
            outputs=[chatbot, msg_input, tts_player],
        )

        mic_input.stop_recording(
            on_mic_stop,
            inputs=[mic_input, chatbot],
            outputs=[chatbot, tts_player],
        )

        # ---------- V2.3:语音免提模式 ----------
        # VAD 分句器:模块级单例(localhost 单用户;切模式时 reset)
        from reachymini_conversation.voice_loop import EnergyVAD

        _voice_vad = EnergyVAD()

        def on_chat_mode_change(mode: str) -> dict:
            """💬/🎤 切换:文本组件 ↔ 流式麦克风 的可见性;chat_mode 写 bus
            (real_voice 线程据此决定是否采真机麦克风)。"""
            logger.info("[voice] chat_mode 切换 → %s", mode)  # P0-2 埋点
            _voice_vad.reset()
            get_state_bus().update("chat_mode", mode)
            run_mode = get_state_bus().get("run_mode", "pure_sim")
            voice = mode == "voice"
            return {
                msg_input: gr.update(visible=not voice),
                mic_input: gr.update(visible=not voice),
                # 浏览器免提麦只在 pure_sim+voice 用;real+voice 走真机麦克风
                voice_mic: gr.update(visible=voice and run_mode == "pure_sim"),
                real_voice_hint: gr.update(visible=voice and run_mode != "pure_sim"),
            }

        chat_mode_radio.change(
            on_chat_mode_change,
            inputs=[chat_mode_radio],
            outputs=[msg_input, mic_input, voice_mic, real_voice_hint],
        )

        async def on_voice_stream(
            audio: tuple[int, np.ndarray] | None,
            history: list[dict[str, Any]],
        ):
            """流式 chunk → VAD 分句 → 完整语句跑 pipeline(async 生成器,
            只有"说完一句"时才 yield 更新,其余 chunk 静默)。 """
            # P0-2 埋点:首个 chunk 记 INFO(证明浏览器流式麦已推流到后端),
            # 之后每 100 chunk 记 DEBUG 防刷屏。用户开免提后日志无
            # "首个 stream chunk" → 组件未推流(权限/组件层),音频未到后端。
            if not hasattr(on_voice_stream, "_chunk_count"):
                on_voice_stream._chunk_count = 0
            on_voice_stream._chunk_count += 1
            if on_voice_stream._chunk_count == 1:
                if audio is not None:
                    logger.info(
                        "[voice] 首个 stream chunk: sr=%s, shape=%s, dtype=%s",
                        audio[0], getattr(audio[1], "shape", None),
                        getattr(audio[1], "dtype", None),
                    )
                else:
                    logger.info("[voice] 首个 stream chunk 为 None")
            if audio is None:
                return
            sample_rate, data = audio
            seg = _voice_vad.push(sample_rate, data)
            if seg is None:
                return
            # 完整语句:VAD 输出是原始采样率,转 16k PCM 给 ASR
            try:
                pcm = numpy_to_pcm16_bytes(sample_rate, seg)
            except Exception as e:
                logger.warning(f"[voice] 音频转换失败: {e}")
                return
            if not pcm:
                return
            pipeline = get_pipeline()
            pipeline.tool_deps = _build_tool_deps()
            result = await pipeline.run_audio(pcm)
            if result.error:
                history = history + [
                    {"role": "user", "content": "🎤 (语音)"},
                    {"role": "assistant", "content": f"🎤 {result.error}"},
                ]
                yield {chatbot: history}
                return
            history = history + [
                {"role": "user", "content": f"🎤 {result.user_text}"},
                {"role": "assistant", "content": result.reply_text or "(无回复)"},
            ]
            yield {
                chatbot: history,
                tts_player: result.audio_path if result.audio_path else gr.update(),
            }

        voice_mic.stream(
            on_voice_stream,
            inputs=[voice_mic, chatbot],
            outputs=[chatbot, tts_player],
        )

        def clear_chat() -> list[dict[str, Any]]:
            return []

        clear_btn.click(clear_chat, inputs=[], outputs=[chatbot])

        # P3 设置面板:保存 → 写 env.json + reload_config
        def save_settings(
            llm_key: str,
            llm_base: str,
            llm_model: str,
            asr_key: str,
            edge_voice: str,
        ) -> str:
            """收集输入 → 调 env_loader.save_env() → reload_config → 状态。"""
            cur = load_env()
            # 留空 = 不修改(用户友好)
            new_env = dict(cur) if cur else {}

            # doubao_llm
            llm = dict(new_env.get("doubao_llm", DEFAULT_ENV["doubao_llm"]))
            if llm_key:
                llm["api_key"] = llm_key
            llm["base_url"] = llm_base or DEFAULT_ENV["doubao_llm"]["base_url"]
            llm["model"] = llm_model or DEFAULT_ENV["doubao_llm"]["model"]
            new_env["doubao_llm"] = llm

            # doubao_asr
            asr = dict(new_env.get("doubao_asr", DEFAULT_ENV["doubao_asr"]))
            if asr_key:
                asr["api_key"] = asr_key
            new_env["doubao_asr"] = asr

            # edge_tts
            tts = dict(new_env.get("edge_tts", DEFAULT_ENV["edge_tts"]))
            tts["voice"] = edge_voice or DEFAULT_ENV["edge_tts"]["voice"]
            new_env["edge_tts"] = tts

            # 校验
            unknown = validate_env(new_env)
            try:
                path = save_env(new_env)
                _root_config.reload_config()
                msg = f"✅ 已保存到 `{path}`(0600)。重启进程后生效。"
                if unknown:
                    msg += f"\n\n⚠️ env.json 包含未识别字段:{unknown}"
                return msg
            except Exception as e:
                logger.exception("save_env failed")
                return f"❌ 保存失败:{type(e).__name__}: {e}"

        save_btn.click(
            save_settings,
            inputs=[
                llm_api_key_input,
                llm_base_url_input,
                llm_model_input,
                asr_api_key_input,
                edge_voice_input,
            ],
            outputs=[save_status],
        )

    return demo


# ============================================================================
# Mock LLM(P4 已废弃,保留以防设置未配时回退)
# ============================================================================
def _mock_llm_reply(user_msg: str) -> str:
    """(P4 备用)设置未配 API Key 时回退到 mock。"""
    return f"(未配 API Key — mock 回复)收到:{user_msg!r}\n\n请在右侧 ⚙️ 设置 填 API Key 后重试。"


# ============================================================================
# 渲染辅助(全部输出 pill HTML:图标 + 文字 + 语义色,不只靠颜色传达状态)
# ============================================================================
def _render_status(snapshot: dict[str, Any]) -> str:
    status = snapshot.get("status", "unknown")
    error = snapshot.get("error")
    label, cls = STATUS_BADGE.get(status, ("❓ Unknown", "pill--muted"))
    if error:
        label, cls = f"🔴 Error: {error}", "pill--err"
    return f'<span class="pill {cls}" role="status">{label}</span>'


def _render_run_mode(run_mode: str, real_connecting: bool = False) -> str:
    if real_connecting:
        return '<span class="pill pill--busy">🟠 连接真机中…</span>'
    if run_mode == "pure_sim":
        cls, icon = "pill--ok", "🧪"
    else:
        cls, icon = "pill--busy", "🤖"
    return f'<span class="pill {cls}">{icon} 模式 <code>{run_mode}</code></span>'


def _render_doa(snapshot: dict[str, Any]) -> str:
    """P5 声源定位 UI(pill);V2:available 但开关关闭时显"已关闭"。"""
    available = snapshot.get("doa_available", False)
    if not available:
        return '<span class="pill pill--muted">🧭 声源 · 不可用(无 ReSpeaker / 纯 sim)</span>'
    if not snapshot.get("doa_follow_enabled", False):
        return '<span class="pill pill--muted">🧭 声源跟随 · 已关闭</span>'
    angle = snapshot.get("doa_angle")
    if angle is None:
        return '<span class="pill pill--busy">🧭 声源 · ⏳ 等待第一帧…</span>'
    speech = snapshot.get("doa_speech", False)
    speech_txt = "🗣️ 说话中" if speech else "🔇 静音"
    cls = "pill--accent" if speech else "pill--ok"
    return f'<span class="pill {cls}">🧭 声源 · {speech_txt} · yaw {angle:+.1f}°</span>'


def _render_hand(snapshot: dict[str, Any]) -> str:
    """P6 手部跟随 UI(pill)。"""
    available = snapshot.get("hand_available", False)
    if not available:
        return '<span class="pill pill--muted">🖐 手部跟随 · 模型不可用</span>'
    enabled = snapshot.get("hand_follow_enabled", False)
    visible = snapshot.get("hand_visible", False)
    uv = snapshot.get("hand_uv")
    if not enabled:
        return '<span class="pill pill--muted">🖐 手部跟随 · 已关闭</span>'
    if visible and uv:
        return f'<span class="pill pill--ok">🖐 跟随中 · 手掌 ({uv[0]}, {uv[1]})</span>'
    return '<span class="pill pill--ok">🖐 跟随中 · ⏳ 等待手出现…</span>'


def _mjpeg_img_html(
    *,
    url: str,
    alt: str,
    available: bool,
    min_height: int = 320,
    max_height: int = 480,
) -> str:
    """MJPEG <img> 容器(带 onerror 自愈重连,三处视频区共用)。

    P7.C:onerror 自动重连。浏览器 <img> 的 MJPEG 连接一旦失败不会自己重试,
    而 Gradio tick 返回相同 HTML 时不更新 DOM → 裂图会永远卡住。
    失败后 2s 带 cache-busting query 重连,流恢复后浏览器侧也能自愈。

    max_height:图片最大显示高度(等比缩放,不裁剪)。主区副视角用 320 限高,
    否则 640x480 流在宽列里撑到 480px+,左右列严重失衡(2026-09-17 UI 调优)。
    """
    src = url if available else ""
    onerror = (
        f'onerror="var s=this;setTimeout(function(){{'
        f"s.src='{url}?t='+Date.now();}},2000);\""
        if src
        else ""
    )
    return f"""
<div style="background:#000;border:1px solid #2A3442;border-radius:8px;overflow:hidden;display:flex;align-items:center;justify-content:center;min-height:{min_height}px;">
  <img src="{src}" style="max-width:100%;max-height:{max_height}px;display:block;" alt="{alt}"
       {onerror} />
</div>
""".strip()


def _scene_feed_html(*, scene_available: bool) -> str:
    """主区:MuJoCo 场景流(studio_close 第三人称,640x640)。"""
    return _mjpeg_img_html(
        url="http://localhost:7861/scene_feed",
        alt="MuJoCo scene feed (studio_close)",
        available=scene_available,
    )


# ============================================================================
# P0-1:TTS 自动播放(WebAudio 一次性解锁方案)
# ----------------------------------------------------------------------------
# 浏览器 autoplay policy:页面无用户手势前 <audio autoplay>(gr.Audio)被静音
# 拦截。改由 tts_autoplay.js:首个手势 resume() AudioContext 解锁,之后轮询
# 7861 /api/tts_state,(path, mtime) 变化即 fetch /api/tts_audio 解码播放。
# `?v=` 版本号防 ES module 强缓存(同 _VIEWER_JS_VERSION 教训,改了记得 bump)。
# ============================================================================
_TTS_AUTOPLAY_JS_VERSION = "20260915b"

_TTS_AUTOPLAY_JS_ON_LOAD = f"""
(async () => {{
  try {{
    const mod = await import('http://localhost:7861/static/js/tts_autoplay.js?v={_TTS_AUTOPLAY_JS_VERSION}');
    mod.mount(element, {{ baseUrl: 'http://localhost:7861' }});
  }} catch (e) {{
    console.error('[reachy-tts] 自动播放模块加载失败:', e);
    const pill = element.querySelector('#reachy-tts-autoplay-pill');
    if (pill) {{
      pill.textContent = '⚠️ 自动播放模块加载失败 — 检查 7861 端口服务';
      pill.dataset.tone = 'warn';
    }}
  }}
}})();
""".strip()

# 状态徽章:图标+文字双语义(不只靠颜色,WCAG 2.1 AA);aria-live 供读屏。
# 音量条:0-200%(Edge TTS 原始响度偏小,允许 2x),GainNode 实时调,
# localStorage 持久化(键 reachy.ttsVolume)。
_TTS_AUTOPLAY_HTML = """
<div class="rm-tts-bar">
  <span id="reachy-tts-autoplay-pill" data-tone="locked" role="status" aria-live="polite">
    🔇 点击页面任意处,启用语音自动播放
  </span>
  <label class="rm-tts-vol" for="reachy-tts-volume">
    🔉
    <input type="range" id="reachy-tts-volume" min="0" max="200" step="5" value="100"
           aria-label="语音播报音量(百分比)">
    <span id="reachy-tts-volume-val" aria-hidden="true">100%</span>
  </label>
</div>
<style>
.rm-tts-bar { display:flex; align-items:center; gap:12px; margin-top:4px; flex-wrap:wrap; }
#reachy-tts-autoplay-pill {
  display:inline-block; padding:6px 12px; border-radius:6px;
  border:1px solid #2A3442; background:#0D1117; color:#8B98AB;
  font-size:12.5px; line-height:1.4;
}
#reachy-tts-autoplay-pill[data-tone="ready"]   { color:#3FB950; border-color:#2B4A33; }
#reachy-tts-autoplay-pill[data-tone="playing"] { color:#58A6FF; border-color:#274B73; }
#reachy-tts-autoplay-pill[data-tone="warn"]    { color:#F0B72F; border-color:#5A4A1F; }
.rm-tts-vol { display:inline-flex; align-items:center; gap:6px; color:#8B98AB; font-size:12.5px; }
.rm-tts-vol input[type="range"] { width:120px; accent-color:#58A6FF; }
#reachy-tts-volume-val { min-width:38px; text-align:right; font-variant-numeric:tabular-nums; }
</style>
""".strip()


# ============================================================================
# V1:three.js 交互式 3D 视图(方案 B)
# ============================================================================
_VIEWER_3D_CONTAINER_ID = "reachy-3d-viewer"

# js_on_load(Gradio 6:`element` = 本 HTML 组件的根 DOM):
#   动态 import 7861 的 three_viewer.js(ES module,CORS 已放行)→ 挂载视图。
#   失败时容器内显示可读错误(例如 7861 没起 / 没跑 export_visual_manifest)。
#   `?v=` 版本号:浏览器对 ES module 有强缓存,改 JS 后不硬刷新会拿旧版
#   (2025-09-15 教训:连杆改动用户看不到)。每次改 viewer 记得 bump。
_VIEWER_JS_VERSION = "20260915b"

_VIEWER_3D_JS_ON_LOAD = f"""
(async () => {{
  const container = element.querySelector('#{_VIEWER_3D_CONTAINER_ID}') || element;
  try {{
    const mod = await import('http://localhost:7861/static/js/three_viewer.js?v={_VIEWER_JS_VERSION}');
    await window.ReachyViewer.mount(container, {{ baseUrl: 'http://localhost:7861' }});
  }} catch (e) {{
    console.error('[reachy-3d] 加载失败:', e);
    container.innerHTML =
      '<div style="display:flex;align-items:center;justify-content:center;height:100%;' +
      'min-height:320px;color:#f85149;font-size:13px;text-align:center;padding:24px;">' +
      '⚠️ 3D 视图加载失败:' + (e && e.message ? e.message : e) +
      '<br><span style="color:#8b98ab">检查 7861 端口服务 / 运行 ' +
      'tools/export_visual_manifest.py</span></div>';
  }}
}})();
""".strip()


def _viewer_3d_html() -> str:
    """V1 主区:three.js 3D 视图容器(canvas 由 js_on_load 注入)。"""
    return f"""
<div id="{_VIEWER_3D_CONTAINER_ID}"
     style="background:#0D1117;border:1px solid #2A3442;border-radius:8px;overflow:hidden;
            width:100%;height:480px;display:flex;align-items:center;justify-content:center;
            color:#8B98AB;font-size:13px;">
  ⏳ 正在加载交互式 3D 视图…
</div>
""".strip()


def _sim_feed_html(*, sim_available: bool) -> str:
    """副视角(sim):机器人眼睛相机流(eye_camera,1280x720),限高 320。"""
    return _mjpeg_img_html(
        url="http://localhost:7861/sim_feed",
        alt="Reachy eye camera feed",
        available=sim_available,
        min_height=180,
        max_height=320,
    )


def _eye_feed_html(*, run_mode: str, eye_available: bool) -> str:
    """副视角:sim 模式 = /sim_feed(眼睛相机,带占位切换);real 模式 = /camera_feed。

    run_mode 从 state_bus 读(决策 3:pure_sim | real_plus_sim)。
    real 模式没有 /camera_feed_status 可轮询,直接挂 <img> + onerror 自愈。
    """
    if run_mode == "pure_sim":
        if eye_available:
            return _sim_feed_html(sim_available=True)
        return SIM_FEED_FALLBACK_HTML
    return _mjpeg_img_html(
        url="http://localhost:7861/camera_feed",
        alt="Real robot camera feed",
        available=True,
        min_height=180,
        max_height=320,
    )


SCENE_FEED_FALLBACK_HTML = """
<div style="background:#0F141C;border:1px solid #2A3442;border-radius:8px;overflow:hidden;
            display:flex;align-items:center;justify-content:center;min-height:320px;
            color:#8B98AB;text-align:center;padding:28px;">
  <div style="max-width:520px;">
    <div style="font-size:40px;opacity:.85;">🌐</div>
    <div style="margin-top:10px;font-size:14px;font-weight:700;letter-spacing:.08em;
                color:#FF8C00;text-transform:uppercase;">
      ⏳ 等待场景视频信号…
    </div>
    <div style="margin-top:10px;font-size:12.5px;line-height:1.8;color:#8B98AB;text-align:left;">
      <div>· daemon 场景渲染线程启动中或链路暂不可用,<b>恢复后本区域自动切回真视频</b></div>
      <div>· 状态轮询:<code>/scene_feed_status</code>(available 双向切换)</div>
      <div>· 链路:daemon studio_close 相机 → GStreamer UDP:5006 → FastAPI MJPEG
        <code>/scene_feed</code>(需经 <code>daemon_launcher</code> 启动 daemon)</div>
      <div>· 排障:见 <code>docs/TROUBLESHOOTING.md</code> 场景流一节</div>
    </div>
  </div>
</div>
""".strip()


SIM_FEED_FALLBACK_HTML = """
<div style="background:#0F141C;border:1px solid #2A3442;border-radius:8px;overflow:hidden;
            display:flex;align-items:center;justify-content:center;min-height:180px;
            color:#8B98AB;text-align:center;padding:28px;">
  <div style="max-width:520px;">
    <div style="font-size:32px;opacity:.85;">🤖</div>
    <div style="margin-top:10px;font-size:13px;font-weight:700;letter-spacing:.08em;
                color:#FF8C00;text-transform:uppercase;">
      ⏳ 等待机器人眼睛视频信号…
    </div>
    <div style="margin-top:10px;font-size:12.5px;line-height:1.8;color:#8B98AB;text-align:left;">
      <div>· 相机初始化中或链路暂不可用,<b>恢复后本区域自动切回真视频</b></div>
      <div>· 状态轮询:<code>/sim_feed_status</code>(available 双向切换)</div>
      <div>· 链路:GStreamer UDP:5005 → daemon media server → SDK 客户端
        → FastAPI MJPEG <code>/sim_feed</code></div>
    </div>
  </div>
</div>
""".strip()


def _scene_feed_status_html(*, available: bool, frames_received: int, frames_attempted: int) -> str:
    """根据 /scene_feed_status 渲染主区 HTML(语义同 _sim_feed_status_html)。"""
    if available:
        return _scene_feed_html(scene_available=True)
    return SCENE_FEED_FALLBACK_HTML


def _sim_feed_status_html(*, available: bool, frames_received: int, frames_attempted: int) -> str:
    """根据 /sim_feed_status 渲染眼睛流 HTML。

    - available=True: 真视频流可用,显示 <img>
    - available=False: 显示带说明文字的占位 (SIM_FEED_FALLBACK_HTML)
    """
    if available:
        return _sim_feed_html(sim_available=True)
    return SIM_FEED_FALLBACK_HTML


async def _fetch_feed_status(endpoint: str) -> dict[str, Any]:
    """轮询 /{endpoint} 拿视频流状态(sim_feed_status / scene_feed_status 通用)。

    返回 dict 含 available / frames_received / frames_attempted。
    拿不到时(daemon/stream 没起)默认 available=False。
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"http://localhost:7861/{endpoint}")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.debug(f"_fetch_feed_status({endpoint}) failed: {type(e).__name__}: {e}")
    return {"available": False, "frames_received": 0, "frames_attempted": 0}


async def _fetch_sim_feed_status() -> dict[str, Any]:
    """(向后兼容包装)轮询 /sim_feed_status。"""
    return await _fetch_feed_status("sim_feed_status")


def _render_meta(snapshot: dict[str, Any]) -> dict[str, Any]:
    last_update = snapshot.get("last_update")
    if last_update is None:
        last_update_str = "never"
    else:
        import time

        age = time.monotonic() - last_update
        last_update_str = f"{age:.1f}s ago"

    return {
        "status": snapshot.get("status"),
        "last_update": last_update_str,
        "error": snapshot.get("error"),
    }


def _flatten_pose(pose: Any) -> list[Any] | None:
    """(P1 保留)把 4x4 矩阵转成可序列化形式给 Gradio JSON。"""
    if pose is None:
        return None
    try:
        import numpy as np

        arr = np.asarray(pose)
        if arr.ndim == 2:
            return arr.tolist()
        return arr.flatten().tolist()
    except Exception:
        return None
