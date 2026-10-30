import os
import time

import pytest

from cloudbridge.config import Mapping
from cloudbridge.core.models import Status
from cloudbridge.core.planner import mapping_key
from cloudbridge.services.uploader import Uploader


def test_upload_stability_incremental_hash_and_remote_deletion(setup):
    cfg, backend, state, source = setup
    path = source / "中文 image.png"
    path.write_bytes(b"first")
    service = Uploader(cfg, backend, state)
    service.run_once()
    assert backend.uploads == 0
    service.run_once()
    assert backend.uploads == 1
    service.run_once()
    assert backend.uploads == 1
    # Same size and preserved mtime still changes the local content digest.
    stat = path.stat()
    path.write_bytes(b"other")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    service.run_once()
    if os.name != "nt":  # Linux detects a changed ctime and observes it again.
        assert backend.uploads == 1
        service.run_once()
    assert backend.uploads == 2
    backend.target("/baidu/GPU-Transfer/Images/中文 image.png").unlink()
    service.run_once()
    assert backend.uploads == 3
    service.run_once(force=True)
    assert backend.uploads == 4
    path.unlink()
    service.run_once()
    assert backend.target("/baidu/GPU-Transfer/Images/中文 image.png").exists()


def test_retry_backoff_exhaustion_force_and_recovery(setup):
    cfg, backend, state, source = setup
    (source / "x.png").write_bytes(b"test")
    backend.failures = 3
    cfg.sync.retry_attempts = 2
    service = Uploader(cfg, backend, state)
    service.run_once()
    service.run_once()
    key = mapping_key("images", str(source), cfg.remote_dir(cfg.sync.mappings[0]))
    row = state.get(key, "x.png")
    assert row["status"] == Status.FAILED and row["attempts"] == 1
    assert row["next_retry"] > time.time()
    service.run_once()
    assert state.get(key, "x.png")["attempts"] == 1
    state.update(key, "x.png", next_retry=0)
    service.run_once()
    assert state.get(key, "x.png")["attempts"] == 2
    state.update(key, "x.png", next_retry=0)
    service.run_once()
    assert backend.failures == 1  # exhausted, no tight loop
    backend.failures = 0
    state.update(key, "x.png", status=Status.TRANSFERRING, attempts=0)
    state.recover()
    service.run_once()
    assert state.get(key, "x.png")["status"] == Status.SUCCESS


def test_rename_atomic_rename_and_multiple_mappings(setup, tmp_path):
    cfg, backend, state, source = setup
    second = tmp_path / "second"
    second.mkdir()
    cfg.sync.mappings.append(Mapping(name="second", local_dir=str(second), remote_dir="Second"))
    (source / "x.tmp").write_bytes(b"temp")
    (second / "x.png").write_bytes(b"second")
    service = Uploader(cfg, backend, state)
    service.run_once()
    (source / "x.tmp").rename(source / "x.png")
    service.run_once()
    assert backend.uploads == 1
    service.run_once()
    assert backend.uploads == 2
    (source / "x.png").rename(source / "renamed.png")
    service.run_once()
    service.run_once()
    assert backend.uploads == 3
    assert backend.target("/baidu/GPU-Transfer/Images/x.png").exists()


def test_duplicate_scan_rejected_and_lock_released(setup):
    cfg, backend, state, source = setup
    service = Uploader(cfg, backend, state)
    with state.scan_lock:
        with pytest.raises(ValueError, match="重复扫描"):
            service.run_once()
    service.run_once()
