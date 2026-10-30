"""Require age and two matching persisted scan observations."""
from __future__ import annotations

from typing import Any

from cloudbridge.core.scanner import LocalFile


def is_stable(file: LocalFile, previous: dict[str, Any] | None, now: float, min_age: float) -> bool:
    return (previous is not None
            and previous["observed_size"] == file.size
            and previous["observed_mtime"] == file.mtime_ns
            and (not file.fingerprint or previous.get("observed_fingerprint") == file.fingerprint)
            and now - file.mtime_ns / 1_000_000_000 >= min_age)
