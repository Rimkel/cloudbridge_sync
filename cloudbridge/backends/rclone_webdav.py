"""rclone WebDAV adapter. No sync, move, purge or delete commands."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cloudbridge.backends.base import BackendError, Cancelled, StorageBackend
from cloudbridge.config import BackendConfig
from cloudbridge.core.models import RemoteFile
from cloudbridge.utils.paths import remote_path
from cloudbridge.utils.subprocess import CommandRunner


class RcloneWebDAVBackend(StorageBackend):
    def __init__(self, config: BackendConfig, mode: str, runner: CommandRunner | None = None) -> None:
        self.config = config
        self.remote = config.remote(mode)
        self.runner = runner or CommandRunner(config.timeout_seconds)

    def _run(self, *args: str) -> str:
        operation = args[0] if args and args[0] in {"listremotes", "lsjson", "copyto", "mkdir", "version"} else "操作"
        if operation == "lsjson":
            operation += "（递归扫描）" if "--recursive" in args else "（元数据查询）" if "--stat" in args else "（目录读取）"
        try:
            return self.runner.run([self.config.executable, *args, "--retries", "1", "--low-level-retries", "1",
                                    "--contimeout", "30s", "--timeout", "60s", "--log-level", "ERROR",
                                    "--dry-run=false", "--interactive=false"])
        except Cancelled:
            raise
        except BackendError as exc:
            raise BackendError(f"rclone {operation}: {exc}", retryable=exc.retryable, code=exc.code) from None

    def _target(self, path: str) -> str:
        return f"{self.remote}:{remote_path(path, root=True)}"

    def check_connection(self) -> None:
        remotes = self._run("listremotes").splitlines()
        if f"{self.remote}:" not in remotes:
            raise BackendError(f"rclone remote 不存在: {self.remote}；请运行 rclone config", retryable=False)
        root = self.get_metadata(self.config.cloud_root)
        if root is None or not root.is_dir:
            raise BackendError("云端根目录不存在或不是目录；请人工创建", retryable=False)
        # Non-recursive listing checks read access without downloading everything.
        self._run("lsjson", self._target(self.config.cloud_root))

    def _decode(self, value: Any) -> RemoteFile:
        try:
            size = int(value["Size"])
            is_dir = bool(value.get("IsDir", False))
            # rclone lsjson --stat reports a directory itself with Path="".
            path = "" if is_dir and value["Path"] == "" else remote_path(value["Path"])
            if size < 0 and not is_dir:
                raise ValueError("未知大小")
            return RemoteFile(path, size, str(value.get("ModTime") or ""),
                              dict(value.get("Hashes") or {}), is_dir)
        except (KeyError, TypeError, ValueError):
            raise BackendError("rclone 返回的文件元数据不合法", retryable=False) from None

    def list_files(self, remote_path: str) -> list[RemoteFile]:
        try:
            data = json.loads(self._run("lsjson", self._target(remote_path), "--recursive", "--files-only", "--hash"))
            if not isinstance(data, list):
                raise ValueError("不是列表")
            return [self._decode(item) for item in data]
        except (ValueError, TypeError):
            raise BackendError("rclone 返回了无效的 JSON 列表", retryable=False) from None

    def get_metadata(self, remote_path: str) -> RemoteFile | None:
        try:
            data = json.loads(self._run("lsjson", self._target(remote_path), "--stat", "--hash"))
            return self._decode(data)
        except BackendError as exc:
            if exc.code in (3, 4) and exc.retryable:
                return None
            raise
        except (ValueError, TypeError):
            raise BackendError("rclone 返回了无效的 JSON 元数据", retryable=False) from None

    def upload(self, local_path: Path, remote_path: str) -> None:
        # The planner already compared local SHA-256 and durable success state.
        # Force the selected file so weak WebDAV timestamps cannot suppress it.
        self._run("copyto", str(local_path.resolve()), self._target(remote_path), "--ignore-times")

    def download(self, remote_path: str, local_path: Path) -> None:
        self._run("copyto", self._target(remote_path), str(local_path.resolve()),
                  "--ignore-times", "--inplace=false")
