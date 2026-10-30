param(
    [string]$DataDirectory = "",
    [string]$OpenListExecutable = "openlist"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $DataDirectory) { $DataDirectory = Join-Path $Root "OpenList\data" }
if (-not (Test-Path -LiteralPath $DataDirectory -PathType Container)) {
    throw "未找到已有 OpenList 数据目录，已停止，未初始化。请用 -DataDirectory 指向原 data 目录。"
}
$DataDirectory = (Resolve-Path -LiteralPath $DataDirectory).Path
$ConfigFile = Join-Path $DataDirectory "config.json"
$DatabaseFile = Join-Path $DataDirectory "data.db"
if (-not (Test-Path -LiteralPath $ConfigFile -PathType Leaf)) {
    throw "未找到原 config.json，已停止，未初始化。请先确认原数据目录。"
}
if (-not (Test-Path -LiteralPath $DatabaseFile -PathType Leaf)) {
    throw "未找到默认 SQLite 数据库 data.db，已停止。若使用自定义数据库，请按原部署方式启动。"
}
$OpenListCommand = Get-Command -Name $OpenListExecutable -CommandType Application -ErrorAction Stop
# Keep relative paths inside the existing OpenList configuration consistent.
Push-Location (Split-Path -Parent $DataDirectory)
try {
    Write-Host "使用已有 OpenList 数据目录: $DataDirectory"
    & $OpenListCommand.Source server --data $DataDirectory --config $ConfigFile
    if ($LASTEXITCODE -ne 0) { throw "OpenList 启动失败，退出码: $LASTEXITCODE" }
} finally {
    Pop-Location
}
