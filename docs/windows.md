# Windows 下载端

目标为 Windows 10 / 11、Python 3.11+、PowerShell 5.1+ 和 rclone。当前开发测试在 Ubuntu 执行；路径解析和服务逻辑经过测试，Windows 原生文件占用、任务计划和大文件场景仍需在真实 Windows 验收。

首次部署见 [图文使用指南](../usage.md)。

在 Windows PowerShell 中进入仓库目录：

```powershell
.\scripts\install_windows.ps1
$CB = '.\.venv\Scripts\cloudbridge.exe'
& $CB --config configs/windows.yaml mapping add --name hdrtv --local 'D:/GPU 图片/HDRTV' --remote HDRTV
& $CB --config configs/windows.yaml config validate
# 先人工完成 OpenList/rclone 授权，确保能访问同一云端路径。
& $CB --config configs/windows.yaml doctor
& $CB --config configs/windows.yaml download --once
# 首次发现等待下一轮云端稳定确认。
& $CB --config configs/windows.yaml download --once
& $CB --config configs/windows.yaml download --daemon
```

安装脚本不修改全局执行策略、不请求管理员权限、不覆盖已有配置。若组织策略禁止脚本执行，请按你所在环境的正常审批流程运行。下载目录可自动创建；示例使用正斜杠避免 YAML 反斜杠转义问题。可在原生 Windows 传入 UNC 绝对路径，但必须具备相应目录权限。

默认保留所有本地文件。未知同名文件或本地已修改文件记录为 `CONFLICT` 并跳过。先人工比对并移动需要保留的文件，再重试；只有明确接受覆盖时才配置 `sync.conflict_policy: overwrite`。下载中断只留下独立暂存文件，最终路径不会用于保存未验证的下载结果。文件占用导致重命名失败时会记录失败并退避重试；不承诺字节级续传。

## 并发下载与首次批量传输

下载端现在实际使用 `sync.max_concurrent_transfers`，全部启用的映射共享同一个并发上限，范围为 1–32。每个任务独立维护相对路径、SQLite 状态和临时文件；校验后才提交最终文件名。单文件失败会独立退避，其他文件继续下载。路径或大小写冲突在该映射启动下载前统一检查。

首次批量传输可自行将 `configs/windows.yaml` 中已有的并发项改为 `4`（保留其他字段）：

```yaml
sync:
  max_concurrent_transfers: 4
```

升级旧版串行下载端时，先双击 `stop-sync.cmd` 并等待停止，再将同一版本的源码更新包解压覆盖到项目根目录。该包不包含用户同步配置、OpenList 数据或凭据，不会自动改变并发数。修改配置后双击 `start-sync.cmd`，新进程会使用新代码及并发设置。没有新依赖，无需重装虚拟环境。

原来配置为 `3` 的用户更新后即会最多同时下载三个文件；改为 `4` 后最多四个。并发是上限，等待稳定、冲突文件、已完成文件和不足上限的批次不会占满任务数。进度窗口的“传输中”计数可显示多个文件，成功计数仍只在校验提交后增加。

调整配置也支持在当前完整扫描周期结束后安全重载；大型首次扫描可能很久，因此若要立即生效，应按上述方式停止后重启。停止时尚未完成的任务由 SQLite 恢复机制重试，已成功文件继续按增量规则跳过。首批传完后可将并发改回 `3` 或你原先设置的值，再停止、启动一次。

已在 Ubuntu 上用受控后端验证并发上限、跨映射共享上限、取消恢复、失败隔离、本地冲突和文件占用错误处理；并用真实 rclone/本机 WebDAV 执行三路并发、六个文件下载及各自 SHA-256 校验。已在一套原生 Windows 环境完成并发恢复与新增 PNG 下载验证；具体范围见 [验收记录](acceptance.md)，这些结果不构成速度承诺。

## 增量扫描与失败任务恢复

对于上次已成功、云端版本未变、目标仍是普通文件且大小匹配的条目，扫描会快速跳过，不重算整个文件的 SHA-256，也不重复提交 SQLite 更新。`SUCCESS` 表示上次传输成功，不表示本轮已重新校验内容。云端出现可检测变化、用户要求重下或目标缺失时，仍使用 SHA-256 检查本地版本，按默认冲突策略保护用户修改；实际下载内容也仍会完整校验再提交。云端未变时，本地同大小修改可能到需要重下时才记录冲突。

每轮日志现在记录总耗时、列目录耗时、快速跳过数、实际下载数、等待与错误数。`interval_seconds` 是完整周期结束后的等待时间；首次文件仍需两次扫描确认稳定。Windows OpenList 的目录缓存与 CloudBridge 扫描间隔分别配置，缓存过长会推迟新文件发现。

临时失败达到 `retry_attempts` 后不会因重启自动重置。修复网络或网关后，可在停止下载进程的情况下只恢复失败队列：

```powershell
.\.venv\Scripts\cloudbridge.exe --config configs/windows.yaml retry-failed --mapping results
```

然后使用原启动入口恢复同步。该命令不立即传输，也不重置成功、冲突或永久失败任务，保留原 SHA-256 与版本历史；不必对整个目录执行 `--force`。省略 `--mapping` 时，只处理当前启用映射。命令通过配置与数据库实例锁拒绝在同步运行时修改队列。

## 当前用户登录任务

验证前台下载正常后显式执行：

```powershell
.\scripts\install_windows_task.ps1 -ConfigPath "$PWD/configs/windows.yaml"
Start-ScheduledTask -TaskName CloudBridge-Download
Get-ScheduledTask -TaskName CloudBridge-Download
Get-ScheduledTaskInfo -TaskName CloudBridge-Download
Stop-ScheduledTask -TaskName CloudBridge-Download
# 不再需要时由用户明确卸载任务：
Unregister-ScheduledTask -TaskName CloudBridge-Download
```

脚本注册当前用户登录后的下载守护进程，低权限运行，不保存密码，不覆盖同名任务，安装后不立即启动。任务设置和应用锁共同阻止重复实例；强制结束任务后的下次启动会恢复 SQLite 中未完成任务。该任务依赖用户登录，不是无人登录时的系统服务。
