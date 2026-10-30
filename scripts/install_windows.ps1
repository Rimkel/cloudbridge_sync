param([string]$ConfigPath = "")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $ConfigPath) { $ConfigPath = Join-Path $Root "configs\windows.yaml" }
& py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else '需要 Python 3.11+')"
if ($LASTEXITCODE -ne 0) { throw "请安装 Python 3.11+ 及 Python Launcher" }
& py -3 -m venv (Join-Path $Root ".venv")
if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败" }
$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -m pip install -e $Root
if ($LASTEXITCODE -ne 0) { throw "安装依赖失败" }
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    & $Python -m cloudbridge --config $ConfigPath config init --mode download
    if ($LASTEXITCODE -ne 0) { throw "创建配置失败" }
} else { Write-Host "保留已有配置: $ConfigPath" }
& $Python -c @'
import sys
from pathlib import Path
from cloudbridge.config import load_config
from cloudbridge.core.state import StateStore
from cloudbridge.utils.paths import native_path
c = load_config(Path(sys.argv[1]))
c.validate_directories(create=True)
native_path(c.logging.file).parent.mkdir(parents=True, exist_ok=True)
with StateStore(native_path(c.state.db_path)):
    pass
'@ $ConfigPath
if ($LASTEXITCODE -ne 0) { throw "初始化目录或 SQLite 失败" }
if (-not (Get-Command rclone -ErrorAction SilentlyContinue)) {
    Write-Host "请从 https://rclone.org/install/ 人工安装 rclone，并重新打开终端。"
}
Write-Host "安装完成：运行 rclone config，添加映射，再执行 doctor。任务计划需显式运行 install_windows_task.ps1。"
