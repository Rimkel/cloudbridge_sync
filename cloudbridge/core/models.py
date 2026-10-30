"""Transfer records shared by storage adapters and services."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
    DISCOVERED = "DISCOVERED"
    WAITING_STABLE = "WAITING_STABLE"
    PENDING = "PENDING"
    TRANSFERRING = "TRANSFERRING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RETRY = "RETRY"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int
    modtime: str = ""
    hashes: dict[str, str] = field(default_factory=dict)
    is_dir: bool = False

    def version(self, *, modtime: bool = False, hashes: bool = False) -> str:
        import json
        return json.dumps([self.size, self.modtime if modtime else "",
                           self.hashes if hashes else {}], sort_keys=True)
