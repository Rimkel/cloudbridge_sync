param([string]$ConfigPath = "", [string]$TaskName = "CloudBridge-Download")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $ConfigPath) { $ConfigPath = Join-Path $Root "configs\windows.yaml" }
$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -m cloudbridge --config $ConfigPath config validate
if ($LASTEXITCODE -ne 0) { throw "配置校验失败" }
& $Python -c "import sys; from pathlib import Path; from cloudbridge.config import load_config; sys.exit(0 if load_config(Path(sys.argv[1])).app.mode == 'download' else '要求 download 配置')" $ConfigPath
if ($LASTEXITCODE -ne 0) { throw "任务要求 download 配置" }
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw "任务已存在，拒绝覆盖: $TaskName"
}
$User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Action = New-ScheduledTaskAction -Execute $Python -Argument "-m cloudbridge --config `"$ConfigPath`" download --daemon" -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings | Out-Null
Write-Host "已注册当前用户登录后启动的任务，当前未启动。"
Write-Host "启动: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "查询: Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "停止: Stop-ScheduledTask -TaskName '$TaskName'"
