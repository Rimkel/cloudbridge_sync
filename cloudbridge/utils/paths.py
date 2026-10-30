"""Path parsing independent of the operating system running validation."""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath


def expand_path(value: str) -> str:
    value = re.sub(r"%([^%]+)%", lambda m: os.environ.get(m[1], m[0]), value)
    return os.path.expanduser(os.path.expandvars(value))


def local_path(value: str) -> PurePath:
    value = expand_path(value)
    if PureWindowsPath(value).drive or value.startswith("\\\\"):
        path = PureWindowsPath(value)
        if not path.is_absolute():
            raise ValueError("Windows 路径必须为绝对路径")
        return path
    return Path(value).resolve()


def native_path(value: str) -> Path:
    parsed = local_path(value)
    if os.name != "nt" and isinstance(parsed, PureWindowsPath):
        raise ValueError("Windows 路径只能在 Windows 上用于实际文件操作")
    return Path(parsed)


def overlaps(a: PurePath, b: PurePath) -> bool:
    if isinstance(a, PureWindowsPath) != isinstance(b, PureWindowsPath):
        return False
    return a == b or a in b.parents or b in a.parents


def remote_path(value: str, *, root: bool = False) -> str:
    if not value or "\\" in value or ":" in value or any(ord(c) < 32 for c in value):
        raise ValueError("无效远端路径")
    parts = value.split("/")
    if any(p in (".", "..") for p in parts):
        raise ValueError("远端路径不允许 . 或 ..")
    if not root and value.startswith("/"):
        raise ValueError("映射远端路径必须为相对路径")
    result = str(PurePosixPath(value))
    if result == ".":
        raise ValueError("远端路径不能为空")
    return result


def safe_target(base: Path, relative: str) -> Path:
    relative = remote_path(relative)
    for part in relative.split("/"):
        if (part.endswith((".", " ")) or any(c in part for c in '<>"|?*')
                or PureWindowsPath(part).is_reserved()):
            raise ValueError(f"远端文件名无法安全映射到 Windows: {part}")
    target = base.joinpath(*relative.split("/"))
    # Refuse symlinks, including dangling links and the final destination.
    current = target
    while current != base:
        if current.is_symlink():
            raise ValueError("目标路径包含符号链接")
        current = current.parent
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError("目标越出下载目录")
    return target
