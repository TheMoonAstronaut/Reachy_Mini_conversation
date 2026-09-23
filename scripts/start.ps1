# =============================================================================
# start.ps1 — Windows 启动脚本(原生适配,与 start.sh 等价)
# =============================================================================
# 用法(在 PowerShell 里,项目根目录):
#   .\scripts\start.ps1                # 纯仿真(默认)
#   .\scripts\start.ps1 -Wired         # 有线真机(启动即自动连 USB 机器人)
#   .\scripts\start.ps1 -Robot         # on-robot(跑在无线版树莓派上;Windows 上一般不用)
#   .\scripts\start.ps1 -NoMedia       # 降级:daemon 不带媒体
#   .\scripts\start.ps1 -DaemonOnly    # 只起 daemon
#
# 前置:
#   1. Python 3.12 并已装依赖:pip install -e ".[dev]"(自动带 gstreamer-bundle)
#   2. 有线模式:机器人 USB 插本机
#   3. 首次运行 PowerShell 脚本若被拦:管理员执行
#      Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#
# 与 start.sh 的差异:
#   - 无声卡初始化(amixer/pactl 是 Linux 工具,Windows 音频由 SDK 直管)
#   - 日志 tee 到 logs\start-<模式>-<时间戳>.log
# =============================================================================
[CmdletBinding()]
param(
    [switch]$Sim,
    [switch]$Wired,
    [switch]$Robot,
    [switch]$NoMedia,
    [switch]$DaemonOnly
)

$ErrorActionPreference = 'Stop'

function Log($msg)  { Write-Host "[start] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[warn] $msg" -ForegroundColor Yellow }
function Err($msg)  { Write-Host "[err] $msg" -ForegroundColor Red }

# ---------- 模式解析(与 start.sh 对齐) ----------
$LaunchMode = "sim"
if ($Robot)   { $LaunchMode = "robot" }
elseif ($Wired) { $LaunchMode = "wired" }

# ---------- Python 环境 ----------
# 优先 conda;否则用已激活的 venv/系统 python
if (Get-Command conda -ErrorAction SilentlyContinue) {
    conda activate reachy
    Log "已激活 conda 环境:reachy"
} else {
    Log "无 conda,使用当前 Python 环境"
}
Log "Python:$(python --version 2>&1)"
Log "PWD:$PWD"

# 校验依赖已装
python -c "import reachy_mini" 2>$null
if ($LASTEXITCODE -ne 0) {
    Err "当前环境未安装 reachy_mini:请先运行 .\scripts\install_deps.ps1"
    exit 1
}

# ---------- 模式注入 ----------
$env:REACHYMINI_RUN_MODE = "pure_sim"
if ($LaunchMode -eq "wired") {
    $env:REACHYMINI_RUN_MODE = "real_plus_sim"
    $env:REACHYMINI_CONN = "wired"
}
if ($LaunchMode -eq "robot") { $env:REACHYMINI_RUN_MODE = "pure_real" }

# ---------- daemon 启动 ----------
$daemonPid = $null
if ($LaunchMode -eq "robot") {
    Log "on-robot 模式:检查本体 daemon(:8000)…"
    try {
        $st = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/daemon/status" -TimeoutSec 5
    } catch {
        Err "本体 daemon 未就绪(robot 模式一般跑在树莓派上,Windows 请用 -Wired)"
        exit 1
    }
    if ($st.state -ne "running") {
        Log "本体 daemon 状态 $($st.state) → 唤醒(wake_up)…"
        Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/daemon/start?wake_up=true" -Method Post | Out-Null
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep 2
            try { $st = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/daemon/status" -TimeoutSec 3 } catch { continue }
            if ($st.state -eq "running") { break }
        }
        if ($st.state -ne "running") { Err "唤醒超时(60s)"; exit 1 }
    }
    Log "本体 daemon 已就绪(不启动 sim daemon)"
} else {
    # sim / wired 都需要本机 sim daemon
    $daemonFlags = @("--sim", "--headless")
    if ($NoMedia) { $daemonFlags += "--no-media"; Warn "-NoMedia:sim 视频流将不可用(UI 显示占位图)" }

    $reuse = $false
    try {
        $st = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/daemon/status" -TimeoutSec 2
        if ($st.state -eq "running") { $reuse = $true }
    } catch {}

    if ($reuse) {
        Log "8000 已有健康 sim daemon,直接复用(跳过新起)"
    } else {
        Log "启动 sim daemon:python -m reachymini_conversation.daemon_launcher $($daemonFlags -join ' ')"
        $proc = Start-Process -FilePath "python" `
            -ArgumentList (@("-m", "reachymini_conversation.daemon_launcher") + $daemonFlags) `
            -RedirectStandardOutput "$env:TEMP\reachy-daemon.log" `
            -RedirectStandardError "$env:TEMP\reachy-daemon.err.log" `
            -PassThru -WindowStyle Hidden
        $daemonPid = $proc.Id
        Log "Daemon PID:$daemonPid,日志:`$env:TEMP\reachy-daemon.log"

        Log "等待 daemon 就绪(轮询 :8000)…"
        $ready = $false
        for ($i = 0; $i -lt 60; $i++) {
            try {
                Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/daemon/status" -TimeoutSec 2 | Out-Null
                $ready = $true; break
            } catch {
                if ($proc.HasExited) { Err "daemon 进程提前退出,日志见 `$env:TEMP\reachy-daemon*.log"; exit 1 }
                Start-Sleep 1
            }
        }
        if (-not $ready) { Err "等待 daemon 就绪超时(60s)"; exit 1 }
        Log "daemon 已就绪"
    }

    if ($DaemonOnly) {
        Log "仅 daemon 模式。Ctrl+C 退出(不会杀 daemon,它是独立进程)。"
        while ($true) { Start-Sleep 5 }
    }
}

# ---------- 启动应用 ----------
Log "启动 Reachy Mini Conversation..."
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Reachy Mini Conversation 已启动($LaunchMode 模式)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
$lanIp = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*"
} | Select-Object -First 1).IPAddress
if ($lanIp) {
    Log "本机访问:   http://localhost:7860"
    Log "局域网访问: http://${lanIp}:7860  (同 WiFi 设备可打开,注意带 http:// 前缀)"
    Write-Host ""
}

New-Item -ItemType Directory -Force "logs" | Out-Null
$logFile = "logs\start-${LaunchMode}-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"
Log "UI 模式(python -m reachymini_conversation --ui),日志 tee 到 $logFile"
python -m reachymini_conversation --ui 2>&1 | Tee-Object -FilePath $logFile

# ---------- 收尾 ----------
if ($daemonPid) {
    Log "关闭 daemon (PID $daemonPid)…"
    Stop-Process -Id $daemonPid -Force -ErrorAction SilentlyContinue
}
