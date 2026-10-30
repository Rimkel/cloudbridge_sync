"""Non-secret launcher settings and safe SSH command construction."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
import yaml

from cloudbridge.config import Model


class Peer(Model):
    project_dir: str
    sync_config: str
    mode: Literal["upload", "download"]
    openlist_executable: str = "openlist"
    openlist_data_dir: str = ""
    openlist_config_file: str = ""
    openlist_url: str = "http://127.0.0.1:5244"
    openlist_management: Literal["auto", "managed", "external"] = "auto"
    openlist_service: str = "openlist.service"

    @field_validator("openlist_service")
    @classmethod
    def service_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*\.service", value):
            raise ValueError("openlist_service 必须是有效的 systemd 服务名")
        return value

    @field_validator("openlist_url")
    @classmethod
    def local_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("OpenList 地址必须是不含凭据的本机 HTTP 地址")
        _ = parsed.port
        return value

    def path(self, value: str) -> Path:
        path = Path(os.path.expandvars(value)).expanduser()
        return path.resolve() if path.is_absolute() else (Path(self.project_dir) / path).resolve()

    @property
    def config_path(self) -> Path:
        return self.path(self.sync_config)

    @property
    def runtime_dir(self) -> Path:
        # Per config rather than one global launcher: independent profiles coexist.
        return Path(str(self.config_path) + ".launcher")


class Profile(Model):
    local: Peer
    remote: Peer
    ssh_host: str = "gpu-server"
    refresh_seconds: float = Field(default=3, ge=1, le=60)

    @field_validator("ssh_host")
    @classmethod
    def safe_host(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", value):
            raise ValueError("ssh_host 必须是已有 SSH 主机别名或 user@host")
        return value


def load_profile(path: Path) -> Profile:
    path = path.expanduser().resolve()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError:
        raise ValueError("一键配置 YAML 语法无效") from None
    profile = Profile.model_validate(data)
    project = Path(os.path.expandvars(profile.local.project_dir)).expanduser()
    profile.local.project_dir = str((path.parent / project).resolve() if not project.is_absolute() else project.resolve())
    if profile.local.mode != "download" or profile.remote.mode != "upload":
        raise ValueError("一键模式要求 local=download，remote=upload")
    if not profile.remote.project_dir.startswith("/"):
        raise ValueError("远端 project_dir 必须是 Ubuntu 绝对路径")
    return profile


def encode_peer(peer: Peer) -> str:
    return base64.urlsafe_b64encode(peer.model_dump_json().encode()).decode()


def decode_peer(value: str) -> Peer:
    try:
        return Peer.model_validate(json.loads(base64.urlsafe_b64decode(value).decode()))
    except (ValueError, UnicodeError):
        raise ValueError("一键运行参数无效") from None


def ssh_command(profile: Profile, action: str) -> list[str]:
    # SSH runs a remote shell even when the local subprocess has shell=False.
    # Quote EVERY remote argument, including user-configurable project paths.
    # This path belongs to Ubuntu, regardless of the controller's OS.
    # Native Path would turn it into backslashes on Windows before SSH sends it.
    python = str(PurePosixPath(profile.remote.project_dir) / ".venv/bin/python")
    remote = shlex.join([python, "-m", "cloudbridge.launcher", "agent", action,
                         "--peer", encode_peer(profile.remote),
                         "--interval", str(profile.refresh_seconds)])
    return ["ssh", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3", profile.ssh_host, remote]
