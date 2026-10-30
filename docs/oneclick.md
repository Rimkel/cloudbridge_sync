# Windows 一键同步与进度预览

先按 [完整使用指南](../usage.md) 完成两端安装、OpenList/rclone 授权和小文件验证。两端使用同一版本 CloudBridge；Windows 已能通过自己的 SSH 配置登录 Ubuntu。SSH 仅管理进程、读取状态，文件经网盘传输。

## 首次配置

停止同一配置的前台同步或既有服务。第一次将 [模板](../configs/oneclick.example.yaml) 复制为 configs/oneclick.yaml，然后编辑；已有文件请保留并按需修改。

```yaml
ssh_host: gpu-server  # 换成已有 SSH 别名或 user@host
refresh_seconds: 3
local:
  project_dir: ..
  mode: download
  sync_config: configs/windows.yaml
  openlist_executable: openlist
  openlist_data_dir: OpenList/data
  openlist_management: managed
  openlist_url: http://127.0.0.1:5244
remote:
  project_dir: /home/your-user/cloudbridge  # 换成真实 Ubuntu 绝对路径
  mode: upload
  sync_config: configs/ubuntu.yaml
  openlist_executable: openlist
  openlist_data_dir: ''
  openlist_management: auto
  openlist_service: openlist.service
  openlist_url: http://127.0.0.1:5244
```

local.project_dir 相对于该 YAML；本机其他路径相对于项目根目录。remote.project_dir 是 Ubuntu 绝对路径，使用正斜杠。两端同步目录继续配置在 ubuntu.yaml / windows.yaml，不在此处重复填写。不要在 YAML 中放密码或 token。

双击 setup-oneclick.cmd 执行检查。如果忘了复制模板，脚本会在缺少时创建配置，但仍需要你填入自己的路径和服务器后重试。它不会自动发现你的 SSH 主机。首次 SSH 主机密钥按现有 SSH 规则确认，需要密码时在终端输入。

## OpenList 管理方式

| 模式 | 行为与适用场景 |
| --- | --- |
| managed | 使用原有数据目录启动/复用本机 OpenList，适合 Windows 独立程序 |
| auto | 发现本用户运行的实例；原数据目录为空时也可复用已运行的指定系统服务 |
| external | 只检查已有本机 OpenList 公共接口，适合服务或容器，不负责启动它 |

Windows 默认尝试项目内 OpenList/openlist.exe 或 PATH 中的程序。其他位置请填写 openlist_executable 的完整路径。OpenList/data 必须是**原有**包含 data.db 与 config.json 的目录；自定义配置文件可填 openlist_config_file。启动器拒绝用新空目录代替原有实例。

managed 模式固定 --data、--config 和工作目录，避免重启后存储“消失”。auto / external 不会执行 sudo，不会修改或启停系统服务。容器、服务停止时按原部署方式恢复。

识别结果保存到同步配置旁的 .launcher/peer.json。健康接口可达不等于网盘授权有效，还需 doctor 检查。修改一键路径前停止同步；变更网关路径时也先按原方式停止旧网关，入口不会强行替换占用端口的实例。

## 四个入口

| 文件 | 行为 |
| --- | --- |
| setup-oneclick.cmd | 首次检查，不启动文件传输 |
| start-sync.cmd | 启动 Ubuntu 上传、Windows 下载与需要管理的网关，打开进度 |
| view-progress.cmd | 只查看进度，不重新启动同步 |
| stop-sync.cmd | 停止本入口拥有的同步进程，保留 OpenList |

启动会处理所有启用映射内的符合规则文件，包括未传输的历史文件。关闭进度、Ctrl+C 或 SSH 状态连接断开后，后台同步继续；重复点击会复用本入口的运行实例。

停止入口使用实例标识请求优雅停止，不批量结束同名进程。SSH 断开时，本地停止仍会执行，远端可能继续；恢复连接后再次停止。其他前台或系统服务启动的实例应按其原方式停止。

默认不安装开机启动，不改 SSH、EasyTier、代理或全局 PowerShell 执行策略。Windows 注销/关机、Ubuntu 重启后需要重新启动；用户会话策略也可能回收后台进程。通过 SSH 从 Ubuntu 直接远程拉起 Windows 进程，不属于这个本机双击入口的使用方式。

## 进度与错误

显示累计成功、等待、传输中、失败、冲突、当前文件及最近成功时间。它不是总字节进度或完成百分比；历史移除映射也可能有旧记录。心跳过期会显示未知状态，心跳正常也不保证账号有效。

同步日志通过以下命令读取；启动器自身的日志与心跳在 configs/windows.yaml.launcher，Ubuntu 对应 ubuntu.yaml.launcher：

```powershell
.\.venv\Scripts\cloudbridge.exe --config configs/windows.yaml logs --lines 30
.\.venv\Scripts\cloudbridge.exe --config configs/windows.yaml doctor
```

| 错误 | 处理 |
| --- | --- |
| SSH 找不到远端 Python | 核对 Ubuntu 项目绝对路径与 .venv；两端更新到同版本 |
| No module named cloudbridge.launcher | 更新该端源码并重新安装依赖 |
| 原 data.db/config.json 不完整 | 指向原目录，不要重新初始化 |
| 已有实例占用 | 停止原来的前台/服务实例后再启动 |
| HTTP 401 / 403、expired_token | 在报错端修复 OpenList/rclone 授权及权限 |
| 新文件等待 | 查稳定性观察、过滤、扫描耗时和 OpenList 缓存 |

核心生命周期、参数引用、实例隔离和进度快照有自动化测试；真实后台 PNG 传输已验证。Windows 双击、密码交互、登录任务及重启等完整组合仍需在各自环境检查，见 [验收范围](acceptance.md)。
