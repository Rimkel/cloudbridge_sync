"""A controllable filesystem backend; never represents a real cloud service."""
import shutil
import threading
from pathlib import Path

import pytest

from cloudbridge.backends.base import BackendError, StorageBackend
from cloudbridge.config import Config
from cloudbridge.core.models import RemoteFile
from cloudbridge.core.planner import sha256
from cloudbridge.core.state import StateStore


class LocalBackend(StorageBackend):
    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.uploads = 0
        self.downloads = 0
        self.failures = 0
        self.partial = False
        self.on_download = None
        self.lock = threading.Lock()

    def target(self, remote):
        return self.root / remote.lstrip("/")

    def check_connection(self):
        pass

    def get_metadata(self, remote):
        path = self.target(remote)
        if not path.exists():
            return None
        return RemoteFile(path.name, path.stat().st_size, str(path.stat().st_mtime_ns),
                          {"SHA-256": sha256(path)} if path.is_file() else {}, path.is_dir())

    def list_files(self, remote):
        root = self.target(remote)
        return [RemoteFile(p.relative_to(root).as_posix(), p.stat().st_size,
                           str(p.stat().st_mtime_ns), {"SHA-256": sha256(p)})
                for p in root.rglob("*") if p.is_file()]

    def upload(self, local, remote):
        with self.lock:
            if self.failures:
                self.failures -= 1
                raise BackendError("simulated offline")
            self.uploads += 1
        target = self.target(remote)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, target)

    def download(self, remote, local):
        with self.lock:
            self.downloads += 1
        local.parent.mkdir(parents=True, exist_ok=True)
        if self.partial:
            local.write_bytes(b"partial")
            raise BackendError("simulated interruption")
        shutil.copyfile(self.target(remote), local)
        if self.on_download:
            self.on_download()


@pytest.fixture
def setup(tmp_path):
    source = tmp_path / "中文 source"
    source.mkdir()
    cfg = Config.model_validate({
        "sync": {"min_file_age_seconds": 0, "retry_base_seconds": .01,
                 "mappings": [dict(name="images", local_dir=str(source), remote_dir="Images")]},
        "state": {"db_path": str(tmp_path / "state.db")},
        "logging": {"file": str(tmp_path / "logs/app.log")},
    })
    backend = LocalBackend(tmp_path / "cloud")
    with StateStore(Path(cfg.state.db_path)) as state:
        yield cfg, backend, state, source
