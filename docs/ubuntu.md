# Ubuntu 上传端

支持 Python 3.11+，建议 Ubuntu 22.04 / 24.04。运行 `bash scripts/install_ubuntu.sh [配置路径]`，脚本创建项目虚拟环境、安装依赖、初始化 SQLite 和日志目录；已有配置保留。若缺少 venv/ensurepip，按系统提示人工安装匹配的 Python venv 包。脚本不安装系统包、不修改代理、SSH 或 EasyTier。

```bash
source .venv/bin/activate
cloudbridge --config configs/ubuntu.yaml mapping add --name hdrtv \
  --local '/your/experiments' --remote HDRTV
cloudbridge --config configs/ubuntu.yaml mapping add --name lineart \
  --local '/your/lineart/outputs' --remote Lineart
cloudbridge --config configs/ubuntu.yaml config validate
cloudbridge --config configs/ubuntu.yaml doctor
```

在 [账号配置](openlist.md) 完成后手动执行 `doctor --write-test`。这会上传并回读唯一文本文件，不自动删除云端测试文件。文本文件不受常规图片过滤器影响；常规文本同步需在 `filters.include` 添加 `*.txt`。

```bash
cloudbridge --config configs/ubuntu.yaml upload --once
# 等至少 interval_seconds 后再次执行；也可直接使用 --daemon。
cloudbridge --config configs/ubuntu.yaml upload --once
cloudbridge --config configs/ubuntu.yaml status
cloudbridge --config configs/ubuntu.yaml upload --daemon
```

Ctrl+C / SIGTERM 会取消在途 rclone，等待工作线程退出，保留任务供下次启动恢复。首次发现、变化后首次观察、修改时间过新的文件都会等待。原子重命名后的最终文件按新文件处理。任务失败按指数退避，网络认证错误需要人工修复。

## 上传扫描与内容缓存

Ubuntu 上传端先枚举所有映射并记录新文件观察，再处理已稳定的新增、变化和重试任务，最后核对历史成功文件。历史云端版本通过每映射一次目录列表读取；列表缺项会再查询单个对象，避免仅凭缓存漏项就重复上传。确认远端已删除时仍会补传，不传播任何删除。

成功上传保存已验证 SHA-256 对应的本地指纹：大小、修改时间、Linux 变更时间、设备和文件身份。后续指纹和云端可检测版本均未变化时，不再读取整份历史文件内容，也不重复写 SUCCESS。同大小且恢复 mtime 的常规 Linux 内容修改会改变 ctime，仍须重新稳定观察和计算哈希。仅修改时间或权限而内容没变的文件，在稳定后更新指纹，不重复上传。

任何实际上传，包括远端删除后的修复和 `--force`，仍在上传前完整计算 SHA-256，上传后复核内容及指纹，并校验远端大小及可靠哈希（如果后端提供）。文件在扫描、哈希或上传期间变化时，不会被当作一个稳定成功版本。

旧状态库新增 `observed_fingerprint` 和 `hashed_fingerprint` 两列，首次升级先补观测，再完整读取历史内容一次，与原成功 SHA-256 比对后建立缓存；内容和云端版本都未变时保留原成功时间，不重复上传。之后不再每轮重算历史哈希。初始化期间可能暂时增加读盘时间。

指纹是本地文件系统的变更检测信号，不是对恶意元数据恢复或不可靠网络文件系统的内容证明。Windows ctime 语义不同，Windows 上传模式保守保留完整内容检查；该性能缓存面向 Ubuntu，不影响 Windows 下载端。

日志每轮显示总耗时、云端列目录耗时、快速跳过、哈希文件数和实际上传数。需要缩短轮间等待时，可把 `sync.interval_seconds` 从 120 调至 30，保留 `min_file_age_seconds: 120` 和两次稳定观察。配置中的间隔仍是整轮结束后的等待时间，实际两轮开始之间还包含扫描和传输耗时；无需为了快而降低文件稳定性要求。

## systemd 用户服务

确认手动运行正常后执行以下步骤。安装脚本拒绝覆盖已有服务，不会默认启动或启用开机运行。服务文件使用明确的仓库、虚拟环境及配置绝对路径，包含空格和中文可用。

```bash
bash scripts/install_systemd.sh "$PWD/configs/ubuntu.yaml"
systemctl --user start cloudbridge-upload
systemctl --user status cloudbridge-upload
systemctl --user stop cloudbridge-upload
systemctl --user restart cloudbridge-upload
journalctl --user -u cloudbridge-upload -n 50
# 仅在需要用户会话启动时自动运行后显式执行：
systemctl --user enable cloudbridge-upload
```

用户服务是否在注销后继续运行取决于系统的用户会话策略；脚本不会启用 linger。rclone 应在用户服务的 PATH 可见，或在 YAML 中为 `backend.executable` 指定绝对路径；若使用私有 `RCLONE_CONFIG`，需在用户自行管理的服务环境中配置它。
