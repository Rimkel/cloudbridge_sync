# CloudBridge

**把远程 Ubuntu 服务器生成的图片和文档，自动中转到 Windows 本地。**

CloudBridge 使用 OpenList + rclone，通过百度网盘连接上传端与下载端。一个 Python 仓库，分别配置两端目录；支持增量扫描、稳定性检查、并发传输、失败重试和 Windows 一键启动。

![CloudBridge 文件传输与管理路径](docs/assets/architecture.svg)

## 为什么开发

训练和推理程序持续在远程 GPU 服务器生成图片，本地电脑需要及时查看。直接通过 SSH 或虚拟组网拉取大量文件时，带宽和链路稳定性容易成为瓶颈；手动打包、上传、下载又打断工作。

CloudBridge 把文件传输交给网盘中转，把 SSH 保留为可选的启动和状态查询通道。目标是让“生成结果后等它自动到本地”成为日常工作方式，优先保证完整性、恢复能力和简单部署。

## 已有功能

- Ubuntu 自动上传，Windows 自动下载，保留相对目录结构。
- 自定义多组目录、文件类型和并发数；YAML 配置配合 CLI 管理。
- 两次稳定观察后传输；Ubuntu 历史成功文件复用内容指纹，减少重复读盘。
- SQLite 记录进度与重试；失败队列可单独恢复，成功文件按增量规则跳过。
- 下载暂存并校验后提交；默认保护本地冲突文件，不传播删除。
- Windows 双击启动、查看进度和停止；也支持各端独立后台部署。

适合训练结果、推理图片和论文素材的单向分发。默认只筛选常见图片；PDF、DOCX、Markdown、LaTeX 等可按 [使用指南](usage.md#5-选择文件类型) 添加。Windows 修改不会反向同步。

## 快速开始

准备 Python 3.11+、rclone，以及两端各自可用的 OpenList 百度网盘挂载。账号授权、WebDAV 用户权限和 rclone remote 需要自行配置；[图文使用指南](usage.md) 从安装开始逐步说明。

**Ubuntu 上传端**，在仓库根目录运行：

```bash
bash scripts/install_ubuntu.sh
source .venv/bin/activate
cloudbridge --config configs/ubuntu.yaml mapping add \
  --name results --local "$HOME/training-output" --remote Results
cloudbridge --config configs/ubuntu.yaml doctor
cloudbridge --config configs/ubuntu.yaml upload --daemon
```

源目录需已存在。默认云端根目录为 `/baidu/GPU-Transfer`，请在 OpenList 创建其下的 `Results` 目录；使用其他挂载路径时先修改 YAML。

**Windows 下载端**，在同版本仓库根目录运行：

```powershell
.\scripts\install_windows.ps1
.\.venv\Scripts\cloudbridge.exe --config configs/windows.yaml mapping add `
  --name results --local 'D:/GPU-Results' --remote Results
.\.venv\Scripts\cloudbridge.exe --config configs/windows.yaml doctor
.\.venv\Scripts\cloudbridge.exe --config configs/windows.yaml download --daemon
```

两端 `backend.cloud_root` 和 `remote_dir` 必须对应同一云端目录；本地路径各自配置。首次发现要等下一轮确认稳定，上传默认还需距最后修改至少 120 秒。

手动链路确认正常后，按 [一键启动说明](docs/oneclick.md) 设置，日常双击 `start-sync.cmd`；关闭进度窗口后后台继续同步。首次安装不会替你配置账号或开启开机启动。

## 使用边界

- 已在真实 Ubuntu → 百度网盘 → Windows 环境验证新增 PNG 自动传输和两端 SHA-256 一致；详见 [测试与验收](docs/acceptance.md)。
- 普通 WebDAV 元数据可能不足以识别**同路径、同大小的内容替换**；源文件变化能触发 Ubuntu 上传，但 Windows 不一定能自动识别这种云端更新。建议为新结果使用新文件名；不能把大小相同视为内容一致。
- 扫描、稳定性观察和 OpenList 缓存都会产生等待；速度取决于网盘、账号、网络和文件规模。
- 中断任务会重试，但不承诺字节级断点续传。大文件中断、Windows 文件占用和完整故障恢复仍需更多真实环境验收。
- OpenList、rclone 和百度网盘独立维护、分别授权；本项目不包含它们的二进制或账号凭据。

## 文档与开发

| 内容 | 入口 |
| --- | --- |
| 从零安装、配置与日常使用 | [usage.md](usage.md) |
| Windows 一键管理两端 | [一键启动](docs/oneclick.md) |
| 后台服务 | [Ubuntu](docs/ubuntu.md) / [Windows](docs/windows.md) |
| 授权与常见错误 | [OpenList](docs/openlist.md) / [故障排查](docs/troubleshooting.md) |
| 模块职责、测试与改进方向 | [开发说明](docs/development.md) |
| 发布内容与核对步骤 | [发布说明](docs/release.md) |

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
```

测试默认不访问百度账号；本地真实 WebDAV 测试需要另外指定 rclone 路径。反馈问题请附操作系统、版本、脱敏后的命令与错误，勿附 token、密码、私有 rclone 配置或 OpenList 数据目录。

## 许可证

[MIT](LICENSE)。OpenList 和 rclone 的许可证以各自项目为准。
