[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Run scripts\install_windows.ps1 first.'
}
$ProfilePath = Join-Path $ProjectRoot 'configs\oneclick.yaml'
if (-not (Test-Path -LiteralPath $ProfilePath)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'configs\oneclick.example.yaml') -Destination $ProfilePath
    Write-Host 'Created configs\oneclick.yaml. Existing sync/OpenList settings were preserved.'
}
& $Python -m cloudbridge.launcher --profile $ProfilePath check
exit $LASTEXITCODE
