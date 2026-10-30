# 开发说明与后续方向

## 模块职责

| 目录 / 文件 | 职责 |
| --- | --- |
| cloudbridge/cli.py、config.py | 命令入口、严格配置校验、原子保存 |
| core/scanner.py、stability.py、planner.py | 筛选、两次稳定观察、版本判断 |
| core/state.py、locking.py、routes.py | SQLite、实例锁、防止本机路径循环 |
| core/scheduler.py、transfer.py | 扫描周期、取消、重试与恢复 |
| services/uploader.py、downloader.py | 增量上传/下载与并发任务组织 |
| backends/ | 可替换存储接口，目前实现 rclone WebDAV |
| launcher/ | 可选 SSH 管理、网关生命周期、后台进程与进度 |
| scripts/ | 安装、后台部署、源码更新包 |
| tests/ | 单元、可控后端和可选本地 WebDAV 测试 |

文件数据走 rclone/OpenList/网盘，不走启动器 SSH。系统使用单仓库、CLI、YAML、SQLite，保持无 Web UI、数据库服务器或队列中间件。

## 本地测试

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
CLOUDBRIDGE_TEST_RCLONE=/absolute/path/to/rclone .venv/bin/python -m pytest -q
```

最后一条启用真实 rclone 与临时回环 WebDAV 测试。测试不读取百度凭据，不访问真实网盘。未指定 rclone 时相关集成测试会跳过；Linux 下模拟 Windows 路径不等于原生 Windows 验收。

## 开源后优先整合的部分

这些是规划，以下命令尚未实现，不应当作当前用法。

| 优先级 | 改进 | 用户收益 / 完成标准 |
| --- | --- | --- |
| P0 | 首次配置向导 | 一次收集运行端、目录、remote、已有 OpenList 路径；生成 YAML 并跑只读诊断，失败时明确指出需要修改的字段 |
| P0 | 文件类型预设 | images / paper / all-safe 等预设生成显式过滤规则；增删扩展名有 CLI，两端配置方便对照 |
| P0 | 可重复的发布与平台 CI | 固定版本源码包、校验和、依赖范围与升级说明；至少 Ubuntu/Windows、Python 3.11 与较新版本验证 |
| P1 | 统一安装与升级检查 | 检测 Python、rclone、OpenList 版本；复用原配置；自动下载第三方程序须明确选择并校验来源 |
| P1 | 更清晰的诊断与进度 | 分开显示等待稳定、扫描、缓存延迟、传输、认证错误和冲突；可导出脱敏诊断包 |
| P1 | 统一运行管理 | CLI 提供 start/stop/status，CMD 仅为薄入口；显示是前台、服务还是启动器拥有进程 |
| P2 | 发布版本清单 | 上传侧发布大小与 SHA-256/版本清单，下载侧据此识别同名同大小更新；设计并发、兼容与原子发布后再实现 |

先降低首次使用和升级成本，再扩展功能。保留当前稳定性检查、失败隔离和非破坏性默认行为；暂不引入 Web 管理、双向同步、自动删除或多账号调度。

## 贡献与问题反馈

先说明目标和复现，再提交最小变更与相应测试。公共路径与示例使用通用占位，不写开发机账号、私网 IP、真实数据路径和凭据。只对授权范围内的实际网盘做端到端测试，结果明确区分模拟与真实平台。

变更传输逻辑时优先检查文件变化中的竞争、取消、临时文件提交、失败次数与成功跳过；不要通过降低校验或静默覆盖来消除错误。发布流程见 [release.md](release.md)。
