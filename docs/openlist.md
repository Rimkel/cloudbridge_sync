# OpenList、百度账号与 rclone 人工配置

以下账号授权和凭据配置步骤需要用户参与。每个部署都需要分别配置与验证；开发期间已验证的范围见 [验收记录](acceptance.md)。

1. 自行部署或选择受信任的 OpenList，依据 [OpenList 百度网盘驱动文档](https://doc.oplist.org/guide/drivers/baidu) 完成授权并添加存储。refresh token、client secret 仅输入你管理的私有部署，不发送到代码、测试或聊天记录。
2. 在 OpenList 中确认该账号的实际挂载目录，创建专用中转目录，例如 `/baidu/GPU-Transfer`，及映射目录 `HDRTV`。挂载名不一定是 `baidu`，必须按实际路径修改 YAML。确认使用的 OpenList 用户具有需要的读取、上传权限。
3. 在 Ubuntu 和 Windows 各运行 `rclone config`。选择新 remote，名称如 `baidu`，类型 `webdav`，vendor 选择实际服务对应项（普通 OpenList 可先使用 `other`），填写当前部署的 WebDAV URL 和 OpenList 用户凭据。URL 以你的实例显示为准，不将凭据嵌入 URL。参数依据 [rclone WebDAV 官方文档](https://rclone.org/webdav/)。
4. YAML 仅填写 remote 名称、云端目录和本地映射。可用 `upload_remote` 与 `download_remote` 分离权限；各端私有 rclone 配置必须分别存在。避免将私有配置放入传输目录。
   OpenList 中供 rclone 上传使用的用户需同时开启“WebDAV 读取”“WebDAV 管理”和“创建目录或上传”。仅能读取不代表具有写权限；缺少写权限可能导致创建目录返回 HTTP 403。参见 [官方 WebDAV 权限说明](https://doc.oplist.org/guide/advanced/webdav)。
5. 各端执行 `doctor` 做只读诊断。该检查会访问真实服务，但不会写云端；它不能证明写权限或真实文件回读能力。
6. 在允许留下测试文本的目录执行 `doctor --write-test`。这是明确授权写入：程序先显示路径，然后上传唯一测试文本并回读，比较两份本地文件的 SHA-256。测试云端文件保留，失败后也不自动清理。该测试只验证当前 remote，不替代两台实际机器的完整验收。
7. 按 [验收清单](acceptance.md) 上传、下载真实文本和 PNG，并在两端比对本地 SHA-256。

OpenList 驱动文档指出百度大文件下载存在特定请求头或代理路径的要求。部署时按你使用版本的官方说明手动配置，并实际验证大文件；CloudBridge 不会自动修改全局代理、网络或 OpenList 设置。第三方服务、百度授权和后端能力可能变化，首次部署及升级后都应重新验证上传和下载。

普通 WebDAV 不承诺可靠修改时间或哈希，不要直接打开 `reliable_modtime` / `reliable_hashes`。先测试同大小内容变更、重复查询元数据稳定性及哈希与本地内容一致后再选择是否开启；空哈希不能证明内容一致。
