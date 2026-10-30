"""Incremental scans avoid full content reads while guarding every overwrite."""
import pytest

from cloudbridge.core.models import Status
from cloudbridge.core.planner import mapping_key
from cloudbridge.services.downloader import Downloader


def completed_download(setup):
    config, backend, state, destination = setup
    config.app.mode = "download"
    config.backend.reliable_hashes = True
    relative = "nested/中文 file.png"
    root = config.remote_dir(config.sync.mappings[0])
    remote = backend.target(root + "/" + relative)
    remote.parent.mkdir(parents=True, exist_ok=True)
    remote.write_bytes(b"version 0001")
    service = Downloader(config, backend, state)
    service.run_once()
    service.run_once()
    key = mapping_key("images", str(destination), root)
    assert state.get(key, relative)["status"] == Status.SUCCESS
    return config, backend, state, destination / relative, remote, service, key, relative


def test_unchanged_success_scan_neither_hashes_content_nor_rewrites_state(setup, monkeypatch):
    config, backend, state, target, remote, service, key, relative = completed_download(setup)
    previous = state.get(key, relative)
    import cloudbridge.services.downloader as module
    monkeypatch.setattr(module, "sha256", lambda path: pytest.fail("不变文件不应重新读取内容"))
    monkeypatch.setattr(state, "update", lambda *args, **kwargs: pytest.fail("不变文件不应重写 SQLite 状态"))
    service.run_once()
    assert backend.downloads == 1
    assert state.get(key, relative) == previous
    assert target.read_bytes() == remote.read_bytes()


def test_missing_local_success_file_is_repaired_without_force(setup):
    config, backend, state, target, remote, service, key, relative = completed_download(setup)
    target.unlink()
    service.run_once()
    assert target.read_bytes() == remote.read_bytes()
    assert backend.downloads == 2
    assert state.get(key, relative)["status"] == Status.SUCCESS


def test_remote_change_still_checks_local_digest_before_overwrite(setup):
    config, backend, state, target, remote, service, key, relative = completed_download(setup)
    target.write_bytes(b"user edits!!")
    remote.write_bytes(b"version 0002")
    assert target.stat().st_size == remote.stat().st_size
    service.run_once()  # Observe the changed cloud version.
    service.run_once()  # Confirm stability, then protect the local edit.
    assert target.read_bytes() == b"user edits!!"
    assert backend.downloads == 1
    assert state.get(key, relative)["status"] == Status.CONFLICT


def test_force_checks_content_and_does_not_overwrite_unknown_same_size_local_edit(setup, monkeypatch):
    config, backend, state, target, remote, service, key, relative = completed_download(setup)
    target.write_bytes(b"user edits!!")
    assert target.stat().st_size == remote.stat().st_size
    import cloudbridge.services.downloader as module
    actual = module.sha256
    hashed = []
    def digest(path):
        hashed.append(path)
        return actual(path)
    monkeypatch.setattr(module, "sha256", digest)
    service.run_once(force=True)
    assert target in hashed
    assert target.read_bytes() == b"user edits!!"
    assert backend.downloads == 1
    assert state.get(key, relative)["status"] == Status.CONFLICT


def test_local_size_change_is_not_treated_as_an_unchanged_success(setup):
    config, backend, state, target, remote, service, key, relative = completed_download(setup)
    target.write_bytes(b"different local size")
    service.run_once()
    assert target.read_bytes() == b"different local size"
    assert backend.downloads == 1
    assert state.get(key, relative)["status"] == Status.CONFLICT


def test_same_size_local_edit_is_deferred_until_a_download_is_required(setup, monkeypatch):
    config, backend, state, target, remote, service, key, relative = completed_download(setup)
    previous = state.get(key, relative)
    target.write_bytes(b"user edits!!")
    assert target.stat().st_size == remote.stat().st_size
    import cloudbridge.services.downloader as module
    monkeypatch.setattr(module, "sha256", lambda path: pytest.fail("远端未变，本轮无需读取本地内容"))
    service.run_once()
    assert target.read_bytes() == b"user edits!!"
    assert backend.downloads == 1
    assert state.get(key, relative) == previous  # SUCCESS records the last transfer.


def test_new_file_is_observed_after_many_successes_without_rehashing_them(setup, monkeypatch):
    config, backend, state, destination = setup
    config.app.mode = "download"
    config.backend.reliable_hashes = True
    root = config.remote_dir(config.sync.mappings[0])
    key = mapping_key("images", str(destination), root)
    names = [f"existing {index:03d}.png" for index in range(32)]
    for index, relative in enumerate(names):
        remote = backend.target(root + "/" + relative)
        remote.parent.mkdir(parents=True, exist_ok=True)
        remote.write_bytes(f"existing-content-{index}".encode())
    service = Downloader(config, backend, state)
    service.run_once()
    service.run_once()
    assert backend.downloads == len(names)
    previous = {relative: state.get(key, relative) for relative in names}
    new_name = "zzz newly created.png"
    backend.target(root + "/" + new_name).write_bytes(b"new stable content")
    import cloudbridge.services.downloader as module
    original = module.sha256
    monkeypatch.setattr(module, "sha256", lambda path: pytest.fail("观察新文件无需重读已有内容"))
    service.run_once()
    assert state.get(key, new_name)["status"] == Status.WAITING_STABLE
    assert not (destination / new_name).exists()
    assert backend.downloads == len(names)
    assert {relative: state.get(key, relative) for relative in names} == previous
    monkeypatch.setattr(module, "sha256", original)
    service.run_once()
    assert (destination / new_name).read_bytes() == b"new stable content"
    assert backend.downloads == len(names) + 1
