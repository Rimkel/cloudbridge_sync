"""Guard registered local configurations against upload/download path loops."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from cloudbridge.config import Config, load_config
from cloudbridge.core.locking import InstanceLock
from cloudbridge.utils.paths import local_path, overlaps


def register_routes(config_path: Path, config: Config) -> None:
    folder = Path.home() / ".cloudbridge/locks"
    registry = folder / "routes.json"
    with InstanceLock(folder / "routes.lock"):
        try:
            paths = json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else []
            if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
                raise ValueError("目录注册记录格式错误")
        except (ValueError, OSError):
            raise ValueError(f"无法读取本地目录注册记录: {registry}") from None
        current = str(config_path.resolve())
        retained = []
        for value in paths:
            path = Path(value)
            if value == current or not path.exists():
                continue
            try:
                other = load_config(path)
            except (ValueError, OSError):
                raise ValueError(f"已注册的其他配置无效，请先修复: {path}") from None
            retained.append(value)
            if other.app.mode == config.app.mode:
                continue
            for mapping in config.sync.mappings:
                for previous in other.sync.mappings:
                    if mapping.enabled and previous.enabled and overlaps(local_path(mapping.local_dir), local_path(previous.local_dir)):
                        raise ValueError(f"上传与下载本地目录重叠，会产生传输循环；冲突配置: {path}")
        retained.append(current)
        fd, temporary = tempfile.mkstemp(prefix=".routes-", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(retained, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, registry)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
