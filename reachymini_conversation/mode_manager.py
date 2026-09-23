"""reachymini_conversation.mode_manager — 运行模式切换生命周期管理(V2.1)。

职责(SPEC_V2.md D3):
  pure_sim ↔ real_plus_sim 的运行时切换,含真机连接/断开/daemon B 进程管理。

切换语义:
  switch_to("real_plus_sim"):
    有线(wired):  subprocess 起 daemon B(--fastapi-port 8001,--no-media)
                  → 轮询其 /api/daemon/status 就绪 → 建 client → attach
    无线(wireless): 不起 daemon,直接 client network 连机器人 daemon
    失败路径:      清理半成品(daemon B / client),回滚 pure_sim,返回 error
  switch_to("pure_sim"):
    detach real → 关 client → (wired)杀 daemon B

可测试性:
  client_factory / daemon_runner 全部可注入,测试不碰真进程/真硬件。
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from typing import Any

from reachymini_conversation.state_bus import get_state_bus

logger = logging.getLogger(__name__)

# 有线版 daemon B 的默认端口(sim daemon 在 8000,避开)
REAL_DAEMON_PORT = 8001
# daemon B 就绪轮询超时(真机电机初始化 + wake_up 可能要 30s+)
REAL_DAEMON_READY_TIMEOUT_S = 90.0
# client 健康检查超时
REAL_CLIENT_TIMEOUT_S = 8.0


class RealDaemonRunner:
    """有线版真机 daemon(daemon B)的进程管理。

    起:python -m reachy_mini.daemon.app.main --fastapi-port 8001 --no-media
       (不带 --sim → 真机 backend;--serialport 不传 = SDK 自动找 USB)
    注意:真机 backend 不需要 daemon_launcher 的 mujoco patch(那是 sim 专用)。

    V2 加固(孤儿 daemon 教训):daemon B 以 start_new_session 独立进程组跑,
    宿主 app 重启不会连带杀它 —— 端口/串口会被残留 daemon 占住,新 daemon 起不来。
    所以 start() 前先探测:8001 上已有健康 daemon 就直接收养(不重起)。

    2026-09-15 实测灾难(真机电机被断电):残留 daemon 占着端口但 state=error
    (电机通信故障)时 _probe_ready() 失败,若直接新起,第二个 daemon 会:
    打开 USB 串口成功 → wake_up 电机 → bind 端口失败 → shutdown 钩子执行
    "Putting Reachy Mini to sleep" → 真机电机断电。因此:端口被占但不健康
    时,必须先 TERM/KILL 清理僵尸并等端口释放,才允许新起。
    """

    def __init__(self, port: int = REAL_DAEMON_PORT, log_path: str = "/tmp/reachy-daemon-real.log"):
        self.port = port
        self.log_path = log_path
        self._proc: subprocess.Popen | None = None
        self._adopted = False  # 收养的别人的 daemon,stop() 时不杀

    def _probe_ready(self) -> bool:
        """端口有 daemon 且 backend 真正就绪(state=='running')。

        教训(2025-09-15):只看 HTTP 200 不够 —— 电机没上电时 daemon 的
        uvicorn 也会 200,但 backend 初始化失败,WS 连接被 403 拒。
        """
        import json
        import urllib.request

        url = f"http://127.0.0.1:{self.port}/api/daemon/status"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status != 200:
                    return False
                payload = json.loads(resp.read())
                return payload.get("state") == "running"
        except Exception:
            return False

    def _find_occupant_pid(self) -> int | None:
        """本端口上处于 LISTEN 的进程 PID;无占用或枚举失败 → None。"""
        import psutil

        try:
            for conn in psutil.net_connections(kind="tcp"):
                if (
                    conn.laddr
                    and conn.laddr.port == self.port
                    and conn.status == psutil.CONN_LISTEN
                ):
                    return conn.pid
        except (psutil.Error, PermissionError) as e:
            logger.warning(f"[real-daemon] 枚举端口 {self.port} 占用失败: {e}")
        return None

    def _reap_pid(self, pid: int, timeout_s: float = 8.0) -> None:
        """TERM → 等退出 → 超时 KILL;最后确认端口 LISTEN 已释放。"""
        import psutil

        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return
        try:
            proc.terminate()
            proc.wait(timeout=timeout_s)
        except psutil.TimeoutExpired:
            logger.warning(f"[real-daemon] PID {pid} TERM 超时,升级 KILL")
            try:
                proc.kill()
                proc.wait(timeout=3)
            except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                pass
        except psutil.NoSuchProcess:
            return
        # 确认端口释放(只有 LISTEN 占用会挡新 daemon bind;TIME_WAIT 不挡)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self._find_occupant_pid() is None:
                return
            time.sleep(0.2)
        logger.error(
            f"[real-daemon] 端口 {self.port} 清理后仍被 LISTEN 占用,"
            "新 daemon 可能 bind 失败"
        )

    def start(self) -> None:
        """后台起 daemon B;已有健康 daemon 在端口上则收养(幂等)。

        端口被占但不健康(state != running)时必须先清理僵尸再起新 daemon,
        否则新 daemon bind 失败的 shutdown 钩子会 sleep 真机电机(见类 docstring)。
        """
        if self._proc is not None and self._proc.poll() is None:
            logger.info(f"[real-daemon] 已在运行(PID {self._proc.pid})")
            return
        if self._probe_ready():
            self._adopted = True
            logger.info(f"[real-daemon] 端口 {self.port} 已有健康 daemon,直接收养")
            return
        occupant = self._find_occupant_pid()
        if occupant is not None:
            logger.warning(
                f"[real-daemon] 端口 {self.port} 被 PID {occupant} 占用但不健康"
                "(state != running)—— 清理僵尸 daemon 后重启"
            )
            self._reap_pid(occupant)
        cmd = [
            sys.executable, "-m", "reachy_mini.daemon.app.main",
            "--fastapi-port", str(self.port),
            "--no-media",  # 避开与 sim daemon 的 UDP 5005/5006 冲突;音频走声卡独立通道
            "--log-level", "INFO",
        ]
        log_f = open(self.log_path, "a", encoding="utf-8")
        self._proc = subprocess.Popen(
            cmd, stdout=log_f, stderr=subprocess.STDOUT,
            start_new_session=True,  # 独立进程组,app 退出不连带;停止由 stop() 负责
        )
        logger.info(f"[real-daemon] 已启动 PID {self._proc.pid} @ :{self.port},日志 {self.log_path}")

    def wait_ready(self, timeout_s: float = REAL_DAEMON_READY_TIMEOUT_S) -> bool:
        """轮询 /api/daemon/status 直到 state=='running' 或超时(或进程早退)。

        注意:必须等 backend 就绪(motors 检测到 + wake_up 完成),
        否则 client WS 会被 403 拒("No motors detected" 时尤其如此)。
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                logger.error(
                    f"[real-daemon] 进程提前退出 rc={self._proc.returncode},"
                    f"日志 {self.log_path}(常见原因:真机电源没开 / USB 串口被占)"
                )
                return False
            if self._probe_ready():
                logger.info("[real-daemon] 就绪(state=running)")
                return True
            time.sleep(1.0)
        logger.error(f"[real-daemon] 就绪超时({timeout_s}s)")
        return False

    def stop(self) -> None:
        """停 daemon B(先 TERM 后 KILL);收养的 daemon 不杀(不属于我们)。"""
        if self._adopted:
            logger.info("[real-daemon] 收养的 daemon,不杀,保持运行")
            self._adopted = False
            return
        if self._proc is None:
            return
        if self._proc.poll() is None:
            logger.info(f"[real-daemon] 停止 PID {self._proc.pid}")
            self._proc.terminate()
            try:
                self._proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                logger.warning("[real-daemon] TERM 无效,KILL")
                self._proc.kill()
        self._proc = None


class ModeManager:
    """运行模式切换编排(线程安全,防快速连点)。"""

    def __init__(
        self,
        orchestrator: Any,
        *,
        client_factory: Any | None = None,
        daemon_runner: Any | None = None,
    ) -> None:
        self._orch = orchestrator
        self.bus = get_state_bus()
        self._lock = threading.Lock()
        self._real_mini: Any | None = None
        # 注入点(测试替换;生产用真实现)
        self._client_factory = client_factory or self._default_client_factory
        self._daemon_runner = daemon_runner  # None = 生产时按 wired 现造

    # ---------- 生产默认:真 client ----------
    @staticmethod
    def _default_client_factory(conn_cfg: dict[str, Any]) -> Any:
        from reachy_mini import ReachyMini

        if conn_cfg.get("type") == "wireless":
            return ReachyMini(
                host=conn_cfg.get("host", "reachy-mini.local"),
                port=int(conn_cfg.get("port", 8000)),
                connection_mode="network",
                timeout=REAL_CLIENT_TIMEOUT_S,
            )
        # wired:连本机 daemon B
        return ReachyMini(
            port=int(conn_cfg.get("port", REAL_DAEMON_PORT)),
            connection_mode="localhost_only",
            timeout=REAL_CLIENT_TIMEOUT_S,
        )

    # ---------- 对外 ----------
    @property
    def current_mode(self) -> str:
        return self._orch.run_mode

    def switch_to(self, mode: str, conn_cfg: dict[str, Any]) -> dict[str, Any]:
        """切模式(阻塞式;UI 侧放 async 线程里调)。

        Returns:
            {"ok": bool, "run_mode": str, "error": str | None}
        """
        with self._lock:
            if mode == self._orch.run_mode:
                return {"ok": True, "run_mode": mode, "error": None}
            if self._orch.run_mode == "pure_real":
                return {
                    "ok": False,
                    "run_mode": self._orch.run_mode,
                    "error": "on-robot 模式(纯本体)不支持运行时切换,请重启进程更换启动方式",
                }
            if mode == "real_plus_sim":
                return self._connect_real(conn_cfg)
            if mode == "pure_sim":
                return self._disconnect_real()
            return {"ok": False, "run_mode": self._orch.run_mode, "error": f"未知模式 {mode!r}"}

    # ---------- 内部 ----------
    def _connect_real(self, conn_cfg: dict[str, Any]) -> dict[str, Any]:
        """pure_sim → real_plus_sim。任何一步失败都清理 + 回滚。"""
        self.bus.update("real_connecting", True)
        self.bus.update("error", None)
        daemon: Any | None = None
        mini: Any | None = None
        try:
            # 1. 有线:起 daemon B 并就绪
            if conn_cfg.get("type", "wired") == "wired":
                daemon = self._daemon_runner or RealDaemonRunner(
                    port=int(conn_cfg.get("port", REAL_DAEMON_PORT))
                )
                daemon.start()
                if not daemon.wait_ready():
                    raise RuntimeError(
                        "真机 daemon 未就绪。排查:① 真机电机电源是否接通并打开 "
                        "② USB 线是否插牢 ③ /tmp/reachy-daemon-real.log 末尾有无 "
                        "'No motors detected'"
                    )

            # 2. 建 client(网络握手,可能超时)
            mini = self._client_factory(conn_cfg)

            # 3. 健康检查:能读到状态才算活
            self._health_check(mini)

            # 4. 接入 orchestrator
            self._real_mini = mini
            self._daemon_runner = daemon  # 存住供断开时停
            self._orch.attach_real(mini)
            self.bus.update("run_mode", "real_plus_sim")
            self.bus.update("real_conn_type", conn_cfg.get("type", "wired"))
            self.bus.update("real_connecting", False)
            logger.info("[mode] → real_plus_sim 成功")
            return {"ok": True, "run_mode": "real_plus_sim", "error": None}
        except Exception as e:
            logger.warning(f"[mode] 连接真机失败: {type(e).__name__}: {e}")
            # 清理半成品
            if mini is not None:
                self._close_client_quiet(mini)
            if daemon is not None:
                try:
                    daemon.stop()
                except Exception:
                    pass
            self.bus.update("run_mode", "pure_sim")
            self.bus.update("real_connecting", False)
            err = f"真机连接失败:{type(e).__name__}: {e}"
            self.bus.update("error", err)
            return {"ok": False, "run_mode": "pure_sim", "error": err}

    def _disconnect_real(self) -> dict[str, Any]:
        """real_plus_sim → pure_sim。"""
        mini = self._orch.detach_real()
        self._real_mini = None
        if mini is not None:
            self._close_client_quiet(mini)
        if self._daemon_runner is not None:
            try:
                self._daemon_runner.stop()
            except Exception as e:
                logger.warning(f"[mode] 停 daemon B 失败: {e}")
            self._daemon_runner = None
        self.bus.update("run_mode", "pure_sim")
        self.bus.update("real_conn_type", None)
        logger.info("[mode] → pure_sim")
        return {"ok": True, "run_mode": "pure_sim", "error": None}

    @staticmethod
    def _health_check(mini: Any) -> None:
        """读一次关节位置验证链路活(超时会抛)。"""
        mini.get_current_joint_positions()

    @staticmethod
    def _close_client_quiet(mini: Any) -> None:
        for method in ("close", "disconnect", "client_close"):
            fn = getattr(mini, method, None)
            if callable(fn):
                try:
                    fn()
                except Exception as e:
                    logger.debug(f"[mode] client {method}() 异常(忽略): {e}")
                return


# ============================================================================
# 全局单例(app.py 注入 orchestrator 时创建)
# ============================================================================
_MODE_MANAGER: ModeManager | None = None


def get_mode_manager() -> ModeManager | None:
    return _MODE_MANAGER


def set_mode_manager(mm: ModeManager | None) -> None:
    global _MODE_MANAGER
    _MODE_MANAGER = mm
