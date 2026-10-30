# AGENTS.md — CloudBridge 百度网盘自动文件中转系统

## 1. 项目定位

项目名称：CloudBridge

开发目标：构建基于百度网盘的跨平台自动文件传输系统，解决远程 Ubuntu GPU 服务器与本地 Windows 电脑之间通过 EasyTier/SSH 传输训练图片时，因链路带宽不足、网络不稳定而导致的传输缓慢及断连问题。

系统采用云端中转架构：

```
Ubuntu Server
    │
    ├── 用户自定义监控目录
    │
    ├── 文件变化检测
    │
    ├── 文件稳定性检查
    │
    ├── 增量上传管理器
    │
    └── OpenList + rclone
              │
              ▼
          百度网盘
              │
              ▼
       OpenList + rclone
              │
    ┌─────────┴─────────┐
    │ Windows Client    │
    │                   │
    ├── 增量下载管理器    │
    ├── 自定义保存目录    │
    ├── 自动重试机制      │
    └── 本地图片查看      │
    └───────────────────┘
```

核心原则：

1. Ubuntu 与 Windows 不需要直接建立文件传输连接。
2. EasyTier 继续作为 SSH 管理通道。
3. Ubuntu 负责上传，Windows 负责下载。
4. 使用同一个 Python 项目，通过运行模式区分功能。
5. 所有同步路径均通过配置文件或 CLI 修改，禁止硬编码。
6. 采用非破坏性增量传输，默认不传播删除。
7. 支持断网恢复、进程重启恢复和任务失败重试。
8. 优先保证文件完整性与稳定性，而不是极限传输速度。

## 2. 技术选型

- Python 3.11+
- Ubuntu 22.04 / 24.04
- Windows 10 / 11
- OpenList：百度网盘访问网关
- rclone：文件传输执行器
- PyYAML：配置管理
- Pydantic：配置校验
- SQLite：文件与任务状态持久化
- watchdog：可选的文件变化监听
- logging：日志管理
- pytest：单元测试及集成测试

传输层采取 Adapter 架构。

第一阶段实现：

```
RcloneWebDAVBackend
```

后续可扩展：

- 其他百度网盘访问方式
- 标准 WebDAV 服务
- S3 兼容对象存储

不得假定第三方百度网盘接口始终可用。首次部署必须完成端到端上传、下载能力测试。

## 3. 仓库结构

```
cloudbridge/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── .gitignore
├── .env.example
│
├── configs/
│   ├── ubuntu.example.yaml
│   └── windows.example.yaml
│
├── cloudbridge/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py
│   ├── logging_config.py
│   │
│   ├── core/
│   │   ├── models.py
│   │   ├── scanner.py
│   │   ├── stability.py
│   │   ├── planner.py
│   │   ├── scheduler.py
│   │   ├── transfer.py
│   │   ├── state.py
│   │   └── locking.py
│   │
│   ├── backends/
│   │   ├── base.py
│   │   └── rclone_webdav.py
│   │
│   ├── services/
│   │   ├── uploader.py
│   │   └── downloader.py
│   │
│   └── utils/
│       ├── paths.py
│       └── subprocess.py
│
├── scripts/
│   ├── install_ubuntu.sh
│   ├── install_windows.ps1
│   ├── install_systemd.sh
│   └── install_windows_task.ps1
│
├── tests/
│   ├── test_config.py
│   ├── test_scanner.py
│   ├── test_stability.py
│   ├── test_planner.py
│   ├── test_state.py
│   └── test_transfer.py
│
└── docs/
    ├── ubuntu.md
    ├── windows.md
    ├── openlist.md
    └── troubleshooting.md
```

开发时按照模块职责划分代码。禁止将所有逻辑堆积到一个 Python 文件中。

## 4. 配置管理

### 4.1 Ubuntu 配置

文件：`configs/ubuntu.yaml`

```
app:
  name: CloudBridge
  mode: upload
  log_level: INFO

backend:
  type: rclone_webdav
  rclone_remote: baidu
  cloud_root: "/baidu/GPU-Transfer"

sync:
  interval_seconds: 120
  min_file_age_seconds: 120
  max_concurrent_transfers: 2
  retry_attempts: 5

  mappings:
    - name: hdrtv
      enabled: true
      local_dir: "/home/user/training-output/experiments"
      remote_dir: "HDRTV"
      recursive: true

    - name: lineart
      enabled: false
      local_dir: "/home/user/lineart/outputs"
      remote_dir: "Lineart"
      recursive: true

filters:
  include:
    - "*.png"
    - "*.jpg"
    - "*.jpeg"
    - "*.webp"
    - "*.tif"
    - "*.tiff"
  exclude:
    - "*.tmp"
    - "*.part"
    - "*.partial"
  max_file_size_mb: 0

state:
  db_path: "~/.cloudbridge/state.db"

logging:
  file: "~/.cloudbridge/logs/upload.log"
```

### 4.2 Windows 配置

文件：`configs/windows.yaml`

```
app:
  name: CloudBridge
  mode: download
  log_level: INFO

backend:
  type: rclone_webdav
  rclone_remote: baidu
  cloud_root: "/baidu/GPU-Transfer"

sync:
  interval_seconds: 120
  max_concurrent_transfers: 3
  retry_attempts: 5

  mappings:
    - name: hdrtv
      enabled: true
      remote_dir: "HDRTV"
      local_dir: "D:/GPU-Images/HDRTV"

    - name: lineart
      enabled: false
      remote_dir: "Lineart"
      local_dir: "D:/GPU-Images/Lineart"

state:
  db_path: "~/.cloudbridge/state.db"

logging:
  file: "~/.cloudbridge/logs/download.log"
```

### 4.3 配置要求

必须实现：

- 自定义任意有效的本地目录。
- 自定义百度网盘目录。
- 支持多组目录映射。
- 支持启用或禁用单组映射。
- 自动展开 `~` 和环境变量。
- 兼容 Windows 与 Linux 路径。
- Windows 配置中允许使用正斜杠 `/`。
- 校验目录是否存在、可读、可写。
- 下载目录不存在时允许自动创建。
- 上传源目录不存在时明确报错。
- 检查多组映射之间的本地和远端路径冲突。
- 防止远端目录中的 `..` 等路径穿越。
- 不允许上传任务和下载任务产生路径循环。
- 配置变更在下一次任务启动时生效。
- 常驻模式可以通过安全重新加载配置生效。

同一映射必须保持本地相对路径结构不变。

例如：

```
Ubuntu:
  /experiments/run1/epoch_10.png

Baidu:
  /GPU-Transfer/HDRTV/run1/epoch_10.png

Windows:
  D:/GPU-Images/HDRTV/run1/epoch_10.png
```

### 4.4 目录配置 CLI

必须支持交互式目录配置，不要求用户每次手动编辑 YAML。

例如：

```
cloudbridge config init --mode upload
cloudbridge config init --mode download

cloudbridge mapping list

cloudbridge mapping add \
  --name hdrtv \
  --local "/home/ps/experiments" \
  --remote "HDRTV"

cloudbridge mapping enable hdrtv
cloudbridge mapping disable hdrtv
cloudbridge mapping remove hdrtv
```

Windows 使用相同命令结构。

`mapping add` 在下载模式下将 `--local` 解释为下载目标目录。

所有配置修改应使用原子写入，避免异常退出造成配置文件损坏。

## 5. Ubuntu 上传端

### 5.1 文件扫描

按照配置周期扫描源目录。

第一版采用周期扫描，watchdog 监听作为可选优化。

原因：

- 进程重启后可以恢复扫描。
- 网络断开后可以自动补传。
- 不依赖文件系统事件一定被接收。
- 可以适应大量训练输出文件。

### 5.2 文件稳定性

不能上传正在写入的图片。

采用双重条件：

1. 文件最后修改时间超过指定阈值。
2. 两次扫描所得文件大小与修改时间保持一致。

必须跳过：

- 临时文件。
- 尚未完成写入的文件。
- 用户排除的文件类型。
- 超出用户配置大小限制的文件。

对首次发现且没有历史记录的文件，应等待至少一次稳定性确认，不得直接认定完整。

如训练程序使用临时文件写入再原子重命名，应正确识别最终文件。

### 5.3 增量上传

增量判断必须考虑：

- 相对路径
- 文件大小
- 修改时间
- 已成功上传状态
- 目标文件状态（可获取时）

建议用 SQLite 保存传输状态。

状态模型：

```
DISCOVERED
    ↓
WAITING_STABLE
    ↓
PENDING
    ↓
TRANSFERRING
    ├── SUCCESS
    └── FAILED
          ↓
        RETRY
```

不允许仅因为 SQLite 中存在 `SUCCESS` 记录，就永久跳过目标已被删除的文件。

第一版可以使用 rclone 的非破坏性 `copy` 作为幂等执行基础。

同名文件大小相同但内容不同的问题，必须在设计中明确处理：

- 首选可靠的内容变更判断。
- 若云端后端不提供可靠哈希，使用修改时间、大小及本地传输状态的组合策略。
- 提供强制重新上传功能。
- 不得将 `--size-only` 声称为严格内容一致性保证。

### 5.4 文件删除

默认：

- 不删除云端文件。
- 不删除本地文件。
- 不传播删除事件。

严禁默认执行具有删除语义的 `rclone sync`。

## 6. Windows 下载端

### 6.1 下载规则

周期扫描云端映射目录。

仅下载：

- 本地不存在的文件。
- 云端版本已发生可靠可检测变化的文件。
- 用户明确要求重新下载的文件。

下载后保留云端相对目录结构。

### 6.2 防止下载不完整文件

下载目标需要采用临时文件和最终提交机制。

如果底层调用 rclone，优先使用其正常的临时文件与最终命名行为，并测试：

- 中断下载。
- 重试下载。
- 已存在目标文件。
- 大文件下载。
- Windows 文件占用。

不能让未完成的文件以最终文件名被误判为成功。

### 6.3 本地文件保护

默认不能删除本地文件。

当本地文件与云端文件同名且内容存在冲突时：

- 不静默覆盖无法确认版本关系的本地文件。
- 记录冲突日志。
- 提供可配置冲突处理策略。

建议支持：

```
conflict_policy: skip
```

后续可增加：

```
skip
overwrite
rename
```

默认 `skip`。

## 7. 传输后端

设计统一接口：

```
class StorageBackend(ABC):

    @abstractmethod
    def check_connection(self):
        ...

    @abstractmethod
    def list_files(self, remote_path):
        ...

    @abstractmethod
    def upload(self, local_path, remote_path):
        ...

    @abstractmethod
    def download(self, remote_path, local_path):
        ...

    @abstractmethod
    def get_metadata(self, remote_path):
        ...
```

第一版实现 rclone WebDAV Adapter。

要求：

1. 使用 `subprocess` 的参数数组调用 rclone。
2. 禁止使用未经必要验证的 `shell=True`。
3. 所有路径按参数传递，防止命令注入。
4. 处理返回码、超时和标准错误。
5. 支持取消任务与优雅退出。
6. 不在日志中输出密码、refresh token。
7. rclone 不存在时给出清晰安装提示。
8. 检测 WebDAV 是否可读、可写。
9. 对上传、下载分别进行功能验证。
10. 不假设百度网盘 WebDAV 具备可靠哈希、修改时间或原子操作。

上传与下载应能够分别选择 rclone remote 配置名。

## 8. 任务调度与故障恢复

支持：

```
cloudbridge upload --once
cloudbridge upload --daemon

cloudbridge download --once
cloudbridge download --daemon
```

要求：

- 定期扫描。
- 不允许同一映射出现多个并发扫描任务。
- 同一文件不得同时启动多个传输。
- 支持失败重试。
- 指数退避，设置最大等待时间。
- 区分永久失败与临时网络失败。
- 支持安全退出。
- 重启后恢复未完成任务。
- 避免失败任务无限高速循环。
- 后端认证失效时输出明确错误。

多次重复启动同一配置，应通过进程锁或实例锁避免冲突。

## 9. 日志和状态查询

必须实现 CLI：

```
cloudbridge status
cloudbridge doctor
cloudbridge logs
```

`status` 至少输出：

- 当前运行模式。
- 已启用映射数量。
- 本地目录和云端目录。
- 已完成文件数量。
- 待传输文件数量。
- 失败文件数量。
- 最近成功时间。
- 最近失败原因。

`doctor` 至少检查：

- Python 运行环境。
- 配置文件有效性。
- 本地目录权限。
- rclone 是否安装。
- rclone remote 是否存在。
- OpenList WebDAV 是否可访问。
- 云端目录是否存在。
- 读写权限是否满足当前模式。

检查应区分只读操作、会修改云端的写入测试，并在执行写入测试前明确告知。

## 10. Ubuntu 部署

使用 Python 虚拟环境。

提供：

```
bash scripts/install_ubuntu.sh
```

脚本负责：

- 检查 Python 版本。
- 创建虚拟环境。
- 安装项目依赖。
- 创建默认配置模板。
- 检查 rclone。
- 初始化 SQLite。
- 创建日志目录。
- 展示后续授权与配置步骤。

不能未经用户同意覆盖原有配置。

常驻进程使用 systemd 用户服务，推荐服务名：

```
cloudbridge-upload.service
```

支持：

```
systemctl --user start cloudbridge-upload
systemctl --user stop cloudbridge-upload
systemctl --user restart cloudbridge-upload
systemctl --user status cloudbridge-upload
```

服务工作目录、执行路径与配置路径必须明确。

是否启用开机后台运行，应提供显式安装步骤，不得默认修改系统设置。

## 11. Windows 部署

提供：

```
.\scripts\install_windows.ps1
```

脚本负责：

- 检查 Python。
- 创建虚拟环境。
- 安装依赖。
- 初始化配置。
- 检查 rclone。
- 创建数据目录与日志目录。

提供 Windows 任务计划程序安装脚本：

```
.\scripts\install_windows_task.ps1
```

要求：

- 支持开机登录后自动启动。
- 支持后台周期同步。
- 防止重复进程。
- 支持查询运行状态。
- 不需要管理员权限即可完成用户级部署的部分，应优先使用用户级部署。
- 保证正常 Windows 路径及包含空格、中文的目录可用。

## 12. 测试要求

不得只通过 mock 测试就宣称端到端传输可用。

### 单元测试

覆盖：

- Linux 路径解析。
- Windows 路径解析。
- 目录映射。
- 文件稳定性判断。
- 文件过滤。
- 文件状态流转。
- 重试机制。
- 配置解析。
- 重复任务防护。

### 集成测试

使用本地文件目录和可控的模拟 StorageBackend。

覆盖：

- 新文件上传。
- 已存在文件跳过。
- 内容变化。
- 文件重命名。
- 网络失败。
- 任务中断恢复。
- 多目录映射。
- 文件删除不传播。
- 本地文件冲突。
- 中文路径。
- 空格路径。

### 真实环境验收

在用户授权的百度网盘环境中执行：

1. Ubuntu 上传文本文件。
2. Windows 下载文本文件。
3. Ubuntu 上传 PNG 文件。
4. Windows 下载 PNG 文件。
5. 校验下载文件 SHA-256 与 Ubuntu 原文件一致。
6. 验证自动扫描。
7. 验证断网后的重试。
8. 验证服务重启后的补传。
9. 验证多组映射。
10. 验证不自动删除原始文件。

SHA-256 通过两端本地文件计算，不依赖 WebDAV 提供哈希。

## 13. 开发实施阶段

### Phase 1：项目骨架

- 初始化 Python package。
- 创建 pyproject.toml。
- 实现配置模型。
- 实现目录映射。
- 实现 CLI 骨架。
- 编写配置测试。

完成后确保：

```
cloudbridge --help
cloudbridge config validate
cloudbridge mapping list
```

能够运行。

### Phase 2：传输后端

- 实现 StorageBackend 接口。
- 实现 RcloneWebDAVBackend。
- 完成 rclone 命令封装。
- 处理错误、超时和日志。
- 编写 mock 集成测试。

### Phase 3：Ubuntu 上传模块

- 扫描文件。
- 判断稳定性。
- 上传任务队列。
- SQLite 状态记录。
- 重试与恢复。
- 增量上传。
- 多目录映射。

### Phase 4：Windows 下载模块

- 云端目录扫描。
- 本地增量下载。
- 文件冲突保护。
- 临时文件处理。
- 断点恢复能力验证。

### Phase 5：部署自动化

- Ubuntu 安装脚本。
- Ubuntu systemd 服务。
- Windows 安装脚本。
- Windows 定时任务。
- 启动和停止说明。

### Phase 6：联调与文档

- 完整测试。
- README。
- Ubuntu 部署文档。
- Windows 部署文档。
- 百度网盘授权说明。
- 故障排查说明。

## 14. Codex 工作规则

1. 先检查仓库，再制定修改计划。
2. 尽量使用现有依赖，避免增加不必要的框架。
3. 不要引入 Web 管理后台、数据库服务器或复杂微服务架构。
4. 每完成一个阶段，运行对应测试。
5. 避免在未经测试时声称兼容真实百度网盘。
6. 不得编造百度网盘 API 或 rclone 参数。
7. 接口不确定时先查阅官方文档或本机帮助。
8. 不得把真实账号密码、token 写入代码、测试或提交记录。
9. 不得上传项目目录中的敏感文件。
10. 不得自动删除用户数据。
11. 不得自动提交或推送 Git，除非用户明确要求。
12. 不得未经用户授权修改 Ubuntu 网络、EasyTier 或 SSH 配置。
13. 同一仓库必须支持 Ubuntu 和 Windows。
14. 保持模块化，并提供完整的类型标注和错误处理。
15. 所有公开 CLI 应提供帮助信息及使用示例。

## 15. 项目完成标准

只有满足以下要求，才能认定第一版本完成：

- Ubuntu 端可以自定义一个或多个源目录。
- 可以自定义百度网盘远端目录。
- Windows 端可以自定义一个或多个保存目录。
- Ubuntu 能自动上传新增的稳定文件。
- Windows 能自动下载新增的云端文件。
- 可恢复短暂断网导致的失败任务。
- 已完成的文件不会无故重复传输。
- 默认不会删除两端或云端文件。
- 日志可以定位传输错误。
- 配置和启动方式有完整文档。
- 核心自动化逻辑通过测试。
- 真实百度网盘上传下载经过用户环境验证。

优先交付稳定、简单、可维护的 CLI 版本，不开发 GUI。

在 Ubuntu 上开发时，先完成可运行的上传端、存储接口和跨平台核心模块，再继续 Windows 端的实现。不得为了验证 Windows 功能而在 Ubuntu 上假装运行真实 Windows 系统。