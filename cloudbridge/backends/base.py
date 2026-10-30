"""Storage contract. Missing objects return None; failures must raise."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from cloudbridge.core.models import RemoteFile


class BackendError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True, code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.code = code


class Cancelled(BackendError):
    """Transfer cancelled; preserve it for next startup."""


class StorageBackend(ABC):
    """Adapters must support concurrent per-file calls from transfer workers.

    Keep request/process state local to each call. Directory scans remain serial,
    but another mapping may be listed while existing transfers are finishing.
    """

    @abstractmethod
    def check_connection(self) -> None:
        """Read-only connectivity, configuration and root check."""

    @abstractmethod
    def list_files(self, remote_path: str) -> list[RemoteFile]:
        """Recursively list files under a remote directory."""

    @abstractmethod
    def upload(self, local_path: Path, remote_path: str) -> None:
        """Transfer one file without deleting unrelated files."""

    @abstractmethod
    def download(self, remote_path: str, local_path: Path) -> None:
        """Transfer one file to the caller's staging path."""

    @abstractmethod
    def get_metadata(self, remote_path: str) -> RemoteFile | None:
        """Return None only for an explicitly missing object."""
