# =============================================================================
# install_deps.ps1 — Windows 环境一键准备
# =============================================================================
# 做的事:
#   1. 检查/安装 Git 与 Python 3.12(用 winget;没有 winget 则提示手动装)
#   2. 创建虚拟环境 venv(或用 conda,若已装)
#   3. pip install -e ".[dev]"  —— reachy-mini 会自动拉 gstreamer-bundle
#      (Windows 的 GStreamer + PyGObject 官方方案,无需手动装 GTK!)
#
# 网络提示:国内环境 PyPI 慢/断时,把下面 $PipIndex 换成镜像:
#   https://mirrors.aliyun.com/pypi/simple/
# =============================================================================
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PipIndex = "https://pypi.org/simple"   # 国内可换阿里云镜像

function Log($msg)  { Write-Host "[deps] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[warn] $msg" -ForegroundColor Yellow }
function Err($msg)  { Write-Host "[err] $msg" -ForegroundColor Red }

# ---------- 1. Git ----------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Log "安装 Git for Windows(winget)…"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
    } else {
        Err "未找到 git 且没有 winget。请手动安装:https://git-scm.com/install/windows 后重跑本脚本"
        exit 1
    }
} else { Log "git 已就绪:$(git --version)" }

# ---------- 2. Python 3.12 ----------
$pyOk = $false
try {
    $v = python --version 2>&1
    if ($v -match "3\.1[2-9]") { $pyOk = $true; Log "Python 已就绪:$v" }
} catch {}
if (-not $pyOk) {
    Log "安装 Python 3.12(winget)…"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
        Warn "装完请重开一个 PowerShell 窗口让 PATH 生效,再重跑本脚本"
        exit 0
    } else {
        Err "未找到 Python 3.12 且没有 winget。请手动安装:https://www.python.org/downloads/(安装时勾选 Add python.exe to PATH)"
        exit 1
    }
}

# ---------- 3. 虚拟环境 + 依赖 ----------
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (Get-Command conda -ErrorAction SilentlyContinue) {
    Log "检测到 conda,创建环境:conda create -n reachy python=3.12 -y"
    conda create -n reachy python=3.12 -y
    conda activate reachy
} else {
    Log "创建 venv:.venv\reachy"
    python -m venv .venv\reachy
    .\.venv\reachy\Scripts\Activate.ps1
}

Log "安装项目依赖(pip install -e `".[dev]`")…"
Log "  镜像:$PipIndex(国内慢就编辑本脚本换成阿里云镜像)"
python -m pip install --upgrade pip
pip install -e ".[dev]" -i $PipIndex

Log "验证:reachy-mini-conversation --help"
reachy-mini-conversation --help | Select-Object -First 5

Write-Host ""
Log "环境就绪。启动:"
Log "  .\scripts\start.ps1          # 仿真"
Log "  .\scripts\start.ps1 -Wired   # 有线真机(机器人插 USB)"
Write-Host ""
Warn "提示:首次 pip 安装会触发 gstreamer-bundle 的后安装下载(数十 MB,GStreamer"
Warn "  运行时),属正常现象;失败时检查网络/换镜像后重跑 pip install。"
