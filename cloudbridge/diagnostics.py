"""Read-only doctor by default; write tests require an explicit CLI flag."""
from __future__ import annotations

import sys
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from cloudbridge.backends.base import BackendError
from cloudbridge.backends.rclone_webdav import RcloneWebDAVBackend
from cloudbridge.config import Config
from cloudbridge.core.planner import sha256


def doctor(config: Config, *, write_test: bool = False) -> int:
    print(f"Python: {sys.version.split()[0]}；模式: {config.app.mode}")
    config.validate_directories()
    print("配置与本地目录权限: OK")
    backend = RcloneWebDAVBackend(config.backend, config.app.mode)
    try:
        print(backend._run("version").splitlines()[0])
        backend.check_connection()
        print("rclone remote、WebDAV 连接与云端根目录读取: OK")
        for mapping in config.sync.mappings:
            if mapping.enabled:
                metadata = backend.get_metadata(config.remote_dir(mapping))
                if metadata is None or not metadata.is_dir:
                    raise BackendError(f"映射云端目录不存在: {mapping.name}；请人工创建", retryable=False)
                backend._run("lsjson", backend._target(config.remote_dir(mapping)))
                print(f"映射 {mapping.name} 云端目录可读: OK")
        if write_test:
            # Printed before the first remote mutation; flag is explicit consent.
            remote = str(PurePosixPath(config.backend.cloud_root) / f"cloudbridge-doctor-{uuid.uuid4().hex}.txt")
            print(f"写入测试：即将上传并回读 {remote}；云端测试文件会保留。", flush=True)
            with tempfile.TemporaryDirectory(prefix="cloudbridge-doctor-") as folder:
                source, dest = Path(folder) / "source.txt", Path(folder) / "download.txt"
                source.write_text("CloudBridge WebDAV read/write probe\n", encoding="utf-8")
                backend.upload(source, remote)
                backend.download(remote, dest)
                if sha256(source) != sha256(dest):
                    raise BackendError("写入回读 SHA-256 不一致", retryable=False)
                print("当前 remote 上传、下载及本地 SHA-256 回读验证: OK")
        else:
            print("只读诊断完成；云端写权限和文件内容回读尚未验证。显式运行 doctor --write-test 可执行写入测试。")
    except BackendError as exc:
        print(f"诊断失败: {exc}", file=sys.stderr)
        return 1
    return 0
