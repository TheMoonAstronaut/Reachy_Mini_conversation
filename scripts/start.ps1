# =============================================================================
# start.ps1 — Windows 启动脚本(占位)
# =============================================================================
# TODO(P8):在 Windows 上完整适配
# 计划:
#   - 激活 conda 环境 (reachy)
#   - 启动 reachy-mini-daemon --sim
#   - 启动 python main.py
# 参考 Linux 版本 scripts/start.sh。
# =============================================================================

[CmdletBinding()]
param(
    [switch]$Real,
    [switch]$Ui,
    [switch]$PreloadDatasets,
    [switch]$DaemonOnly
)

$ErrorActionPreference = 'Stop'

Write-Host "[start] Windows 启动脚本(占位)" -ForegroundColor Yellow
Write-Host ""
Write-Host "TODO(P8):在 Windows 上完整适配。" -ForegroundColor Yellow
Write-Host "请参考 scripts/start.sh 的 Linux 版本自行迁移。" -ForegroundColor Cyan
Write-Host ""

# 不退出非零,smoke test 友好
exit 0
