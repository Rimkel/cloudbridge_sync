"""Strict configuration models and atomic YAML persistence."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from cloudbridge.utils.paths import expand_path, local_path, native_path, overlaps, remote_path


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppConfig(Model):
    name: str = "CloudBridge"
    mode: Literal["upload", "download"] = "upload"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class BackendConfig(Model):
    type: Literal["rclone_webdav"] = "rclone_webdav"
    rclone_remote: str = "baidu"
    upload_remote: str | None = None
    download_remote: str | None = None
    cloud_root: str = "/baidu/GPU-Transfer"
    executable: str = "rclone"
    timeout_seconds: float = Field(default=3600, gt=0)
    reliable_modtime: bool = False
    reliable_hashes: bool = False

    @field_validator("rclone_remote", "upload_remote", "download_remote")
    @classmethod
    def validate_remote(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_. -]*", value):
            raise ValueError("remote 名称不能包含路径、连接参数或凭据")
        return value

    @field_validator("cloud_root")
    @classmethod
    def validate_root(cls, value: str) -> str:
        return remote_path(value, root=True)

    def remote(self, mode: str) -> str:
        return (self.upload_remote if mode == "upload" else self.download_remote) or self.rclone_remote


class Mapping(Model):
    name: str = Field(pattern=r"^[\w-]+$")
    enabled: bool = True
    local_dir: str
    remote_dir: str
    recursive: bool = True

    @field_validator("local_dir")
    @classmethod
    def validate_local(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("本地目录不能为空")
        return str(local_path(value))

    @field_validator("remote_dir")
    @classmethod
    def validate_relative(cls, value: str) -> str:
        return remote_path(value)


class SyncConfig(Model):
    interval_seconds: float = Field(default=120, gt=0)
    min_file_age_seconds: float = Field(default=120, ge=0)
    max_concurrent_transfers: int = Field(default=2, ge=1, le=32)
    retry_attempts: int = Field(default=5, ge=1)
    retry_base_seconds: float = Field(default=5, gt=0)
    retry_max_seconds: float = Field(default=300, gt=0)
    conflict_policy: Literal["skip", "overwrite"] = "skip"
    mappings: list[Mapping] = Field(default_factory=list)


class FiltersConfig(Model):
    include: list[str] = Field(default_factory=lambda: ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.tif", "*.tiff"])
    exclude: list[str] = Field(default_factory=lambda: ["*.tmp", "*.part", "*.partial", ".cloudbridge-*"])
    max_file_size_mb: float = Field(default=0, ge=0)


class StateConfig(Model):
    db_path: str = "~/.cloudbridge/state.db"


class LoggingConfig(Model):
    file: str = "~/.cloudbridge/logs/cloudbridge.log"


class Config(Model):
    app: AppConfig = Field(default_factory=AppConfig)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @model_validator(mode="after")
    def validate_mappings(self) -> Self:
        names: set[str] = set()
        for index, mapping in enumerate(self.sync.mappings):
            if mapping.name in names:
                raise ValueError(f"映射名称重复: {mapping.name}")
            names.add(mapping.name)
            for other in self.sync.mappings[:index]:
                if overlaps(local_path(mapping.local_dir), local_path(other.local_dir)):
                    raise ValueError("本地映射目录重叠")
                a, b = PurePosixPath(mapping.remote_dir), PurePosixPath(other.remote_dir)
                # Case folding also prevents common Windows filename collisions.
                a, b = PurePosixPath(str(a).casefold()), PurePosixPath(str(b).casefold())
                if overlaps(a, b):
                    raise ValueError("远端映射目录重叠")
            for internal in (self.state.db_path, self.logging.file):
                if overlaps(local_path(mapping.local_dir), local_path(internal)):
                    raise ValueError("状态数据库和日志不能位于传输目录内")
        return self

    def validate_directories(self, *, create: bool = False) -> None:
        for mapping in self.sync.mappings:
            if not mapping.enabled:
                continue
            path = native_path(mapping.local_dir)
            if create and self.app.mode == "download":
                path.mkdir(parents=True, exist_ok=True)
            if not path.is_dir():
                raise ValueError(f"本地目录不存在: {path}")
            flags = os.R_OK | os.X_OK
            if self.app.mode == "download":
                flags |= os.W_OK
            if not os.access(path, flags):
                raise ValueError(f"本地目录权限不足: {path}")

    def remote_dir(self, mapping: Mapping) -> str:
        return str(PurePosixPath(self.backend.cloud_root) / mapping.remote_dir)


def load_config(path: Path) -> Config:
    path = path.expanduser().resolve()
    try:
        with path.open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except yaml.YAMLError:
        raise ValueError("YAML 语法无效；请检查缩进和引号（不输出原文以保护凭据）") from None
    if not isinstance(data, dict):
        raise ValueError("配置必须是 YAML 对象")
    def resolve_relative(value: object) -> object:
        if isinstance(value, str):
            expanded = expand_path(value)
            if not Path(expanded).is_absolute() and not PureWindowsPath(expanded).drive:
                return str(path.parent / expanded)
        return value
    sync = data.get("sync")
    if isinstance(sync, dict) and isinstance(sync.get("mappings"), list):
        for mapping in sync["mappings"]:
            if isinstance(mapping, dict) and "local_dir" in mapping:
                mapping["local_dir"] = resolve_relative(mapping["local_dir"])
    for section, key in (("state", "db_path"), ("logging", "file")):
        if isinstance(data.get(section), dict) and key in data[section]:
            data[section][key] = resolve_relative(data[section][key])
    return Config.model_validate(data)


def error_message(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "配置校验失败: " + "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'config'}: {item['msg']}"
            for item in error.errors(include_input=False, include_context=False, include_url=False)
        )
    return str(error)


def save_config(path: Path, config: Config) -> None:
    path = Path(expand_path(str(path))).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            yaml.safe_dump(config.model_dump(), stream, allow_unicode=True, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
