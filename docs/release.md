# 发布说明

GitHub 仓库名称为 cloudbridge_sync，Python 包和命令仍为 cloudbridge。许可证为 [MIT](../LICENSE)。当前分发方式为源码加虚拟环境，不宣称已在 PyPI 发布，也不将源码 ZIP 称为免安装程序。

## 公开内容

- Python 源码、测试、部署脚本、示例 YAML。
- README.md、usage.md、通用文档与示意图。
- LICENSE、pyproject.toml、开发规则及 Git 忽略规则。

以下内容保留在各自部署机器，不能加入提交或发行包：真实 YAML、rclone.conf、OpenList 程序和数据、SQLite、日志、虚拟环境、SSH 密钥、token、私有运维文档及备份。

.gitignore 保护常见本地文件，但不能替代提交前审查。docs/private 是开发机的私有历史归档，公开文档不链接它。更新 ZIP 的文档和图示使用明确名单，不递归收集私有文档。

## 本地验证与源码更新包

```bash
.venv/bin/python -m pytest -q
# 指定 rclone 时增加真实本机 WebDAV 测试：
CLOUDBRIDGE_TEST_RCLONE=/absolute/path/to/rclone .venv/bin/python -m pytest -q
.venv/bin/python scripts/build_windows_update.py --output dist/cloudbridge-source-update.zip
sha256sum dist/cloudbridge-source-update.zip
```

包中 PS1 转为 UTF-8 BOM / CRLF，CMD 使用 CRLF，兼容 Windows PowerShell 5.1。包不包含 Python、OpenList、rclone 或私有配置；新用户仍需按 usage.md 安装依赖，老用户先停止同步和备份后更新。

## 发布核对

1. 确定版本、许可证、仓库公开可见性。
2. 检查 git status 与拟提交文件，排除任何真实配置、日志、账号和个人运维记录。
3. 运行适当测试；检查文档链接、YAML 示例、归档内容及干净解压后的可安装性。
4. 将尚未验收的系统/功能保留在 [验收清单](acceptance.md)，不把本地模拟当成原生 Windows 结果。
5. 发布源码并按需创建版本 Release；附校验和、升级步骤、已知限制和真实测试范围。

不要把源码提交、创建仓库和创建 Release 当成同一件事；实际做了哪项就报告哪项。用户部署文件始终独立于发布源码。
