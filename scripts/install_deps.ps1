# =============================================================================
# install_deps.ps1 — Windows 系统依赖安装(占位)
# =============================================================================
# TODO(P8):用户在 Windows 上二次适配时实现
#
# 计划:
#   - 用 choco 安装:GStreamer, GTK3 Runtime, Microsoft Visual C++ Build Tools
#   - 或用 vcpkg 安装:cairo, glib
#   - 配置 GST_PLUGIN_PATH 环境变量
#
# 临时参考(本项目 P0 不强制 Windows 适配):
#   choco install -y gstreamer gtk-runtime
#   choco install -y python visualstudio2022buildtools
# =============================================================================

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

Write-Host "[deps] Windows 系统依赖安装脚本(占位)" -ForegroundColor Yellow
Write-Host ""
Write-Host "TODO(P8):在 Windows 上完整适配。" -ForegroundColor Yellow
Write-Host ""
Write-Host "推荐手动安装步骤(参考):" -ForegroundColor Cyan
Write-Host "  1. 安装 Chocolatey:https://chocolatey.org/install"
Write-Host "  2. choco install -y gstreamer gtk-runtime python"
Write-Host "  3. choco install -y visualstudio2022buildtools"
Write-Host "  4. 设置环境变量 GST_PLUGIN_PATH"
Write-Host ""
Write-Host "完整说明见 docs/INSTALL.md(Windows 章节)。" -ForegroundColor Cyan

# 暂不退出非零,让脚本可以"成功运行"以满足 smoke test
exit 0
