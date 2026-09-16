"""RealDaemonRunner.start() 僵尸 daemon 清理测试(2026-09-15 真机断电事故回归)。

事故背景:残留 daemon 占着 8001 但 state=error(电机通信故障)时,
_probe_ready() 失败 → 旧代码直接新起第二个 daemon → bind 失败 →
shutdown 钩子执行 "Putting Reachy Mini to sleep" → 真机电机被断电。

保护的事:
  1. 端口被不健康 daemon(state=error)占用 → start() 先 TERM/KILL 清理,
     等端口释放,再新起。
  2. 端口被健康 daemon(state=running)占用 → 收养,不杀,不新起。
  3. 端口空闲 → 直接新起。
  4. self._proc 活着(本 runner 起的)→ 幂等返回。

全部用高端口假 daemon(纯 HTTP server)+ mock Popen,不碰真硬件/真 SDK daemon。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from reachymini_conversation import mode_manager  # noqa: E402
from reachymini_conversation.mode_manager import RealDaemonRunner  # noqa: E402

_FAKE_DAEMON_SRC = """
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

state, port = sys.argv[1], int(sys.argv[2])

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = ('{"state": "%s"}' % state).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass

HTTPServer(("127.0.0.1", port), H).serve_forever()
"""

_PORT_BASE = 18300  # 避开 8000/8001/7860/7861 等真实服务端口


def _start_fake_daemon(port: int, state: str) -> subprocess.Popen:
    """起一个假 daemon:HTTP 200 + 可配 state,占住端口 LISTEN。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", _FAKE_DAEMON_SRC, state, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # 等它 bind 完成(LISTEN 出现)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        import socket

        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return proc
        if proc.poll() is not None:
            raise RuntimeError(f"fake daemon 早退 rc={proc.returncode}")
        time.sleep(0.1)
    raise RuntimeError("fake daemon 5s 内未监听端口")


@pytest.fixture()
def popen_spy(monkeypatch):
    """拦截 mode_manager 里的 subprocess.Popen,记录调用。

    注意:mode_manager.subprocess 就是全局 subprocess 模块本体,patch 后
    _start_fake_daemon 的 Popen 也会命中本 spy —— 因此对 `-c` 形式的
    fake daemon 启动必须转发给真 Popen,只有真 daemon 命令才换假进程。
    """
    calls = []
    real_popen = subprocess.Popen

    class _FakeProc:
        pid = 99999

        def poll(self):
            return None  # 活着

    def _spy(cmd, **kwargs):
        if "-c" in cmd:
            return real_popen(cmd, **kwargs)
        calls.append({"cmd": cmd, "kwargs": kwargs})
        return _FakeProc()

    monkeypatch.setattr(mode_manager.subprocess, "Popen", _spy)
    yield calls
    monkeypatch.undo()  # 防泄漏到后续测试(本模块其它 helper 也用 subprocess)


class TestStartZombieReaping:
    def test_reaps_unhealthy_zombie_then_spawns(
        self, popen_spy, tmp_path
    ) -> None:
        """核心回归:state=error 的僵尸占端口 → 清理后新起(不再重复 daemon)。"""
        port = _PORT_BASE + 1
        zombie = _start_fake_daemon(port, "error")
        runner = RealDaemonRunner(port=port, log_path=str(tmp_path / "d.log"))

        runner.start()

        assert zombie.poll() is not None, "僵尸 daemon 应被 TERM/KILL"
        assert len(popen_spy) == 1, "清理后必须新起自己的 daemon"
        assert runner._adopted is False, "清理后新起的 daemon 归我们管(stop 时要杀)"

    def test_adopts_healthy_daemon(self, popen_spy, tmp_path) -> None:
        port = _PORT_BASE + 2
        healthy = _start_fake_daemon(port, "running")
        runner = RealDaemonRunner(port=port, log_path=str(tmp_path / "d.log"))
        try:
            runner.start()
            assert len(popen_spy) == 0, "健康 daemon 应收养,不新起"
            assert runner._adopted is True
            assert healthy.poll() is None, "收养的 daemon 不得被杀"
        finally:
            healthy.terminate()
            healthy.wait(timeout=5)

    def test_free_port_spawns_directly(self, popen_spy, tmp_path) -> None:
        runner = RealDaemonRunner(
            port=_PORT_BASE + 3, log_path=str(tmp_path / "d.log")
        )
        runner.start()
        assert len(popen_spy) == 1
        assert runner._adopted is False

    def test_idempotent_when_own_proc_alive(self, popen_spy, tmp_path) -> None:
        runner = RealDaemonRunner(
            port=_PORT_BASE + 4, log_path=str(tmp_path / "d.log")
        )
        runner.start()
        runner.start()  # 第二次:_proc 活着,应直接返回
        assert len(popen_spy) == 1


class TestStopSemantics:
    def test_stop_kills_own_but_not_adopted(self, popen_spy, tmp_path) -> None:
        """收养语义不回归:收养的 daemon stop() 不杀;自己起的 stop() 杀。"""
        port = _PORT_BASE + 5
        healthy = _start_fake_daemon(port, "running")
        runner = RealDaemonRunner(port=port, log_path=str(tmp_path / "d.log"))
        try:
            runner.start()
            assert runner._adopted is True
            runner.stop()
            assert healthy.poll() is None, "收养的 daemon stop 时必须保留"
        finally:
            healthy.terminate()
            healthy.wait(timeout=5)
