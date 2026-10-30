"""Incremental comparison with explicit weak-metadata limitations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cloudbridge.core.models import RemoteFile, Status


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def needs_upload(previous: dict[str, Any], digest: str, remote: RemoteFile | None,
                 version: str, force: bool) -> bool:
    return (force or remote is None or remote.is_dir or previous["status"] != Status.SUCCESS
            or previous["sha256"] != digest or previous["remote_version"] != version)


def mapping_key(name: str, local: str, remote: str) -> str:
    # Retargeting a mapping must not reuse a previous destination's success.
    return name + ":" + hashlib.sha256(json.dumps([local, remote]).encode()).hexdigest()[:24]
