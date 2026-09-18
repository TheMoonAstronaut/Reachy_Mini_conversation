"""mirror_orchestrator — sim + real 双实例镜像(决策 9,P2)。

目的:同时持有 sim 仿真 + 真机两个 ReachyMini 实例,
任何动作调用都同步镜像到两边,UI 上"左侧 Mujoco 视频"和"真机"实时同步。

运行模式(run_mode,来自 config.RUN_MODE):
  - "pure_sim"      - 只 sim
  - "real_plus_sim" - sim + real 镜像
  - "pure_real"     - 只真机,无 sim(on-robot 模式:app 跑在无线版机身
                      树莓派上,wrapped_run 连的本体 daemon 即真机,
                      实例放在 sim_mini 位供既有代码路径复用)

镜像方法(plan.md §4.1):
  - goto_target / set_target / play_move / look_at_image / look_at_world
  - enable_wobbling / disable_wobbling
  - push_audio_sample(只推 sim,真机的 mic 自己处理)

线程安全:
  - 所有方法都是 async,asyncio.gather 并行调用 sim + real
  - 不用锁,因为 ReachyMini 内部已经做线程同步

占位与 P1 兼容:
  - MovementManager shim 仍在 actions/movement.py,本类不依赖它
  - 本类是 P2 的新模块,不影响其他模块
"""

from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

RunMode = Literal["pure_sim", "real_plus_sim", "pure_real"]


class MirrorOrchestrator:
    """sim + (可选)real 双实例镜像(决策 9)。"""

    def __init__(
        self,
        sim_mini: Any,
        real_mini: Any | None = None,
        run_mode: RunMode = "pure_sim",
    ) -> None:
        self.sim_mini = sim_mini
        self.real_mini = real_mini
        self.run_mode = run_mode

        logger.info(
            f"MirrorOrchestrator: run_mode={run_mode}, "
            f"sim={'on' if sim_mini else 'off'}, "
            f"real={'on' if real_mini else 'off'}"
        )

    # ---------- 头部动作 ----------
    async def goto_target(
        self,
        head: Any = None,
        antennas: Any = None,
        duration: float = 0.5,
        body_yaw: float | None = 0.0,
    ) -> None:
        """镜像调用:sim + real 同步 goto_target。"""
        kwargs = {
            "head": head,
            "antennas": antennas,
            "duration": duration,
            "body_yaw": body_yaw,
        }
        await self._gather("goto_target", kwargs)

    async def set_target(
        self,
        head: Any = None,
        antennas: Any = None,
        body_yaw: float | None = None,
    ) -> None:
        """镜像调用:set_target(SDK 高阶 API,异步非阻塞)。"""
        await self._gather(
            "set_target",
            {"head": head, "antennas": antennas, "body_yaw": body_yaw},
        )

    # ---------- 动作播放 ----------
    async def play_move(self, move: Any) -> None:
        """镜像调用:play_move(DanceMove / Move)。"""
        await self._gather("play_move", {"move": move})

    # ---------- 视觉驱动 ----------
    async def look_at_image(
        self,
        u: float,
        v: float,
        duration: float = 0.3,
    ) -> None:
        """镜像调用:让头部看向图像坐标(u, v)。"""
        await self._gather("look_at_image", {"u": u, "v": v, "duration": duration})

    # ---------- 音频反应 ----------
    def enable_wobbling(self) -> None:
        """启用 sim + real 的音频反应式摆头(同步阻塞调用,内部是 quick)。"""
        self._apply_sync("enable_wobbling")

    def disable_wobbling(self) -> None:
        self._apply_sync("disable_wobbling")

    # ---------- 音频输出 ----------
    def push_audio_sample(self, audio: Any) -> None:
        """推音频到 sim(真机 mic 自己出声)。"""
        try:
            if self.sim_mini is not None and hasattr(self.sim_mini, "media"):
                self.sim_mini.media.push_audio_sample(audio)
        except Exception as e:
            logger.warning(f"push_audio_sample failed: {e}")

    # ---------- 内部:并行/镜像分发 ----------
    async def _gather(self, method_name: str, kwargs: dict[str, Any]) -> None:
        """并行调用 sim + (可选)real 的同名方法。"""
        import asyncio

        sim_coro = self._safe_call(self.sim_mini, method_name, kwargs) if self.sim_mini else None
        real_coro = (
            self._safe_call(self.real_mini, method_name, kwargs)
            if self.real_mini is not None
            else None
        )

        coros = [c for c in (sim_coro, real_coro) if c is not None]
        if not coros:
            return

        results = await asyncio.gather(*coros, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning(
                    f"MirrorOrchestrator.{method_name} partial failure: {type(r).__name__}: {r}"
                )

    def _apply_sync(self, method_name: str) -> None:
        """同步方法镜像(sim + real 立即调用,无 await)。"""
        for inst, label in (
            (self.sim_mini, "sim"),
            (self.real_mini, "real"),
        ):
            if inst is None:
                continue
            try:
                getattr(inst, method_name)()
            except Exception as e:
                logger.warning(
                    f"MirrorOrchestrator.{method_name}({label}) failed: {type(e).__name__}: {e}"
                )

    async def _safe_call(
        self,
        inst: Any,
        method_name: str,
        kwargs: dict[str, Any],
    ) -> Any:
        """call inst.method_name(**kwargs),异常上抛(给 gather 处理)。"""
        method = getattr(inst, method_name, None)
        if method is None:
            raise AttributeError(f"{type(inst).__name__} 没有方法 {method_name}")
        # SDK 方法大多是async; sync 方法也支持
        import inspect

        if inspect.iscoroutinefunction(method):
            return await method(**kwargs)
        return method(**kwargs)

    # ---------- 运行时插拔(V2,模式切换)----------
    def attach_real(self, real_mini: Any) -> None:
        """运行时接入真机实例(切到 real_plus_sim)。"""
        self.real_mini = real_mini
        self.run_mode = "real_plus_sim"
        logger.info("MirrorOrchestrator: real 已接入 → real_plus_sim")

    def detach_real(self) -> Any | None:
        """运行时摘除真机实例(切回 pure_sim),返回被摘的实例(调用方负责关)。"""
        old = self.real_mini
        self.real_mini = None
        self.run_mode = "pure_sim"
        logger.info("MirrorOrchestrator: real 已摘除 → pure_sim")
        return old

    # ---------- 关闭 ----------
    async def close(self) -> None:
        """清理 sim + real 连接。"""
        # ReachyMini 是 context manager,通常由 wrapped_run 处理
        # 这里只 log
        logger.info("MirrorOrchestrator.close()")


def make_orchestrator(
    sim_mini: Any | None,
    real_mini: Any | None = None,
    run_mode: RunMode = "pure_sim",
) -> MirrorOrchestrator:
    """工厂函数:根据 run_mode 决定 real_mini 是否参与镜像。"""
    if run_mode == "real_plus_sim" and real_mini is None:
        logger.warning("run_mode=real_plus_sim 但 real_mini 未提供,降级为 pure_sim")
        run_mode = "pure_sim"

    return MirrorOrchestrator(
        sim_mini=sim_mini,
        real_mini=real_mini if run_mode == "real_plus_sim" else None,
        run_mode=run_mode,
    )


# ============================================================================
# V2 Fix B:工具动作目标适配器
# ============================================================================
class MirroredToolTarget:
    """把 ToolDependencies.reachy_mini 的动作调用镜像到 sim + real 双实例。

    背景(V2 用户反馈):app.py 原来把 sim_mini 直接注入 ToolDependencies,
    dance/play_emotion/move_head/look_at_sound 等工具全部只驱动 sim,
    真机模式跳舞真机不动。工具里的调用面(全项目扫过)只有:
      - async_play_move(move)        dance / play_emotion(async)
      - play_move(move)              play_emotion fallback(sync)
      - goto_target(head=,duration=) move_head(sync)
      - set_target(...)              look_at_sound 等(async/sync 兼容)
    本适配器按同名方法镜像;单边失败只记日志不影响另一边。
    """

    def __init__(self, orchestrator: MirrorOrchestrator) -> None:
        self._orch = orchestrator

    def _instances(self) -> list[Any]:
        return [
            m
            for m in (self._orch.sim_mini, self._orch.real_mini)
            if m is not None
        ]

    async def async_play_move(self, move: Any) -> None:
        import asyncio

        await asyncio.gather(
            *(m.async_play_move(move) for m in self._instances()),
            return_exceptions=True,
        )

    def play_move(self, move: Any) -> None:
        for m in self._instances():
            try:
                m.play_move(move)
            except Exception as e:
                logger.warning(f"[MirroredToolTarget] play_move 失败: {e}")

    def goto_target(self, **kwargs: Any) -> None:
        for m in self._instances():
            try:
                m.goto_target(**kwargs)
            except Exception as e:
                logger.warning(f"[MirroredToolTarget] goto_target 失败: {e}")

    def set_target(self, **kwargs: Any) -> None:
        for m in self._instances():
            try:
                m.set_target(**kwargs)
            except Exception as e:
                logger.warning(f"[MirroredToolTarget] set_target 失败: {e}")

    def look_at_image(self, u: float, v: float, duration: float = 0.3) -> Any:
        results = []
        for m in self._instances():
            try:
                results.append(m.look_at_image(u, v, duration=duration))
            except Exception as e:
                logger.warning(f"[MirroredToolTarget] look_at_image 失败: {e}")
        return results[0] if results else None

    @property
    def media(self) -> Any:
        """媒体属性兜底给 sim(工具一般不直接用;TTS 路由走 real_voice)。"""
        return getattr(self._orch.sim_mini, "media", None)
