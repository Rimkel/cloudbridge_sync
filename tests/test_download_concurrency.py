"""Concurrent downloads against a controlled local backend, not native Windows."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from cloudbridge.backends.base import BackendError, Cancelled
from cloudbridge.core.models import RemoteFile
from cloudbridge.core.planner import mapping_key
from cloudbridge.services.downloader import Downloader


def files_for(setup, count=7):
    config, backend, state, source = setup
    config.app.mode = "download"
    config.backend.reliable_hashes = True
    expected = {}
    for index in range(count):
        relative = f"中文 folder {index % 2}/image {index}.png"
        content = f"distinct content {index:03d}".encode()
        path = backend.target(config.remote_dir(config.sync.mappings[0]) + "/" + relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        expected[relative] = content
    return config, backend, state, source, expected


@pytest.mark.parametrize("limit", [1, 3, 4])
def test_actual_overlap_respects_limit_and_each_file_keeps_its_paths(setup, limit):
    config, backend, state, source, expected = files_for(setup)
    config.sync.max_concurrent_transfers = limit
    service = Downloader(config, backend, state)
    service.run_once()
    assert not list(source.rglob("*.png"))
    release = threading.Event()
    saturated = threading.Event()
    lock = threading.Lock()
    active = peak = 0
    requests = []
    original = backend.download
    def download(remote, stage):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            requests.append(remote)
            if active == limit:
                saturated.set()
        try:
            assert release.wait(5), "下载未达到配置的并发量"
            original(remote, stage)
        finally:
            with lock:
                active -= 1
    backend.download = download
    with ThreadPoolExecutor(max_workers=1) as driver:
        result = driver.submit(service.run_once)
        try:
            assert saturated.wait(5)
            assert state.summary()["counts"]["TRANSFERRING"] == limit
            assert len(requests) == limit  # No unbounded queue of transfers was started.
            assert not list(source.rglob("*.png"))  # Staging names are never final names.
            with pytest.raises(ValueError, match="已有扫描周期"):
                Downloader(config, backend, state).run_once()
        finally:
            release.set()
        result.result(timeout=10)
    assert peak == limit
    assert len(requests) == len(set(requests)) == len(expected)
    assert state.summary()["counts"] == {"SUCCESS": len(expected)}
    for relative, content in expected.items():
        assert (source / relative).read_bytes() == content
    assert not list(source.rglob(".cloudbridge-*"))
    service.run_once()
    assert len(requests) == len(expected)  # Successful files are still incremental.


def test_limit_is_shared_across_multiple_mappings(setup, tmp_path):
    config, backend, state, source, expected = files_for(setup, count=1)
    second = tmp_path / "第二 download"
    second.mkdir()
    from cloudbridge.config import Mapping
    config.sync.mappings.append(Mapping(name="second", local_dir=str(second), remote_dir="Second"))
    other = backend.target("/baidu/GPU-Transfer/Second/other.png")
    other.parent.mkdir(parents=True)
    other.write_bytes(b"second mapping")
    config.sync.max_concurrent_transfers = 2
    service = Downloader(config, backend, state)
    service.run_once()
    original = backend.download
    barrier = threading.Barrier(2)
    def download(remote, stage):
        barrier.wait(timeout=5)  # Both mappings must share the active batch.
        original(remote, stage)
    backend.download = download
    service.run_once()
    assert state.summary()["counts"] == {"SUCCESS": 2}
    assert (second / "other.png").read_bytes() == b"second mapping"
    for relative, content in expected.items():
        assert (source / relative).read_bytes() == content


def test_stop_cancels_active_jobs_then_restart_recovers_without_final_partial_files(setup):
    config, backend, state, source, expected = files_for(setup, count=6)
    config.sync.max_concurrent_transfers = 2
    stop = threading.Event()
    service = Downloader(config, backend, state, stop)
    service.run_once()
    release = threading.Event()
    saturated = threading.Event()
    lock = threading.Lock()
    started = []
    original = backend.download
    def download(remote, stage):
        with lock:
            started.append(remote)
            if len(started) == 2:
                saturated.set()
        assert release.wait(5)
        stage.write_bytes(b"incomplete")
        raise Cancelled("controlled cancellation")
    backend.download = download
    with ThreadPoolExecutor(max_workers=1) as driver:
        result = driver.submit(service.run_once)
        try:
            assert saturated.wait(5)
            stop.set()
        finally:
            release.set()
        result.result(timeout=10)
    assert len(started) == 2
    assert state.summary()["counts"] == {"RETRY": 2, "WAITING_STABLE": 4}
    assert not list(source.rglob("*.png"))
    assert not list(source.rglob(".cloudbridge-*"))
    stop.clear()
    backend.download = original
    Downloader(config, backend, state, stop).run_once()
    assert state.summary()["counts"] == {"SUCCESS": 6}
    for relative, content in expected.items():
        assert (source / relative).read_bytes() == content


def test_parallel_failures_are_isolated_and_honor_backoff_and_permanent_errors(setup):
    config, backend, state, source, expected = files_for(setup, count=6)
    config.sync.max_concurrent_transfers = 3
    config.sync.retry_base_seconds = 10
    service = Downloader(config, backend, state)
    service.run_once()
    original = backend.download
    calls = {}
    lock = threading.Lock()
    def download(remote, stage):
        with lock:
            calls[remote] = calls.get(remote, 0) + 1
            attempt = calls[remote]
        if "image 0.png" in remote and attempt == 1:
            stage.write_bytes(b"partial")
            raise BackendError("controlled network failure")
        if "image 1.png" in remote:
            raise BackendError("controlled permanent failure", retryable=False)
        original(remote, stage)
    backend.download = download
    service.run_once()
    assert state.summary()["counts"] == {"FAILED": 2, "SUCCESS": 4}
    assert not list(source.rglob(".cloudbridge-*"))
    first_calls = calls.copy()
    service.run_once()
    assert calls == first_calls  # No tight retry loop, even while other files succeeded.
    key = mapping_key("images", str(source), config.remote_dir(config.sync.mappings[0]))
    retry_path = next(path for path in expected if "image 0.png" in path)
    row = state.get(key, retry_path)
    assert row["next_retry"] > time.time() and row["attempts"] == 1
    state.update(key, retry_path, next_retry=0)
    service.run_once()
    assert state.summary()["counts"] == {"FAILED": 1, "SUCCESS": 5}
    assert (source / retry_path).read_bytes() == expected[retry_path]


def test_file_commit_permission_error_preserves_existing_target_and_other_jobs(setup, monkeypatch):
    config, backend, state, source, expected = files_for(setup, count=4)
    config.sync.max_concurrent_transfers = 3
    config.sync.conflict_policy = "overwrite"
    relative = next(iter(expected))
    occupied = source / relative
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(b"existing file retained")
    service = Downloader(config, backend, state)
    service.run_once()
    import cloudbridge.services.downloader as module
    original_replace = module.os.replace
    def replace(stage, target):
        if target == occupied:
            raise PermissionError("do not expose arbitrary OS error content")
        original_replace(stage, target)
    monkeypatch.setattr(module.os, "replace", replace)
    service.run_once()
    assert state.summary()["counts"] == {"FAILED": 1, "SUCCESS": 3}
    assert occupied.read_bytes() == b"existing file retained"
    assert "PermissionError" in state.summary()["last_error"]
    assert "do not expose" not in state.summary()["last_error"]
    assert not list(source.rglob(".cloudbridge-*"))


def test_unknown_local_file_is_protected_while_other_downloads_complete(setup):
    config, backend, state, source, expected = files_for(setup, count=4)
    config.sync.max_concurrent_transfers = 3
    relative = next(iter(expected))
    protected = source / relative
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"local user changes")
    service = Downloader(config, backend, state)
    service.run_once()
    service.run_once()
    assert state.summary()["counts"] == {"CONFLICT": 1, "SUCCESS": 3}
    assert protected.read_bytes() == b"local user changes"
    assert backend.downloads == 3


@pytest.mark.parametrize("paths", [
    ["same.png", "same.png"],
    ["same.png", "SAME.png"],
    ["same//child.png", "same/child.png"],
    ["Folder/a.png", "folder/b.png"],
    ["parent.png", "parent.png/child.png"],
    ["parent.png/child.png", "parent.png"],
])
def test_mapping_path_conflicts_fail_before_any_worker_is_started(setup, paths):
    config, backend, state, source = setup
    config.app.mode = "download"
    backend.list_files = lambda remote: [RemoteFile(path, 1) for path in paths]
    with pytest.raises(BackendError, match="冲突"):
        Downloader(config, backend, state).run_once()
    assert backend.downloads == 0
    assert state.summary()["counts"] == {}
