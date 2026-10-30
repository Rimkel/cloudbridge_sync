"""Periodic local scanning: no symlink traversal and no deletion propagation."""
from __future__ import annotations

import fnmatch
import os
import json
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterator

from cloudbridge.config import FiltersConfig, Mapping
from cloudbridge.utils.paths import native_path, remote_path


@dataclass(frozen=True)
class LocalFile:
    path: Path
    relative: str
    size: int
    mtime_ns: int
    fingerprint: str = ""


def stat_fingerprint(stat: os.stat_result) -> str:
    """Bind cached content to a file identity and change metadata, not size alone.

    Linux ctime detects ordinary same-size edits even when mtime is restored.
    This is a change detector, not a cryptographic filesystem guarantee.
    """
    return json.dumps([stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                       stat.st_dev, stat.st_ino], separators=(",", ":"))


def matches(relative: str, size: int, filters: FiltersConfig) -> bool:
    def match(pattern: str) -> bool:
        return (fnmatch.fnmatchcase(relative.casefold(), pattern.casefold())
                or fnmatch.fnmatchcase(relative.rsplit("/", 1)[-1].casefold(), pattern.casefold()))
    if any(part.endswith((".tmp", ".part", ".partial")) or part.startswith(".cloudbridge-")
           for part in relative.casefold().split("/")):
        return False
    if filters.include and not any(match(pattern) for pattern in filters.include):
        return False
    if any(match(pattern) for pattern in filters.exclude):
        return False
    return not filters.max_file_size_mb or size <= filters.max_file_size_mb * 1024 * 1024


def scan(mapping: Mapping, filters: FiltersConfig) -> Iterator[LocalFile]:
    base = native_path(mapping.local_dir)
    def onerror(error: OSError) -> None:
        raise error
    for directory, dirs, files in os.walk(base, followlinks=False, onerror=onerror):
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink()) if mapping.recursive else []
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                stat = path.stat()
                relative = path.relative_to(base).as_posix()
                remote_path(relative)
                if matches(relative, stat.st_size, filters):
                    yield LocalFile(path, relative, stat.st_size, stat.st_mtime_ns, stat_fingerprint(stat))
            except FileNotFoundError:
                continue
