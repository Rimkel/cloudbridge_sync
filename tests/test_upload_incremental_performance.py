"""Metadata caching must not weaken stable-file or content integrity checks."""
import hashlib
import os
import shutil
import time

import pytest

from cloudbridge.core.models import Status
from cloudbridge.core.planner import mapping_key
from cloudbridge.services.uploader import Uploader


def completed_upload(setup, relative="nested/中文 image.png", payload=b"version 0001"):
    config, backend, state, source = setup
    path = source / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    service = Uploader(config, backend, state)
    service.run_once()
    assert backend.uploads == 0
    service.run_once()
    key = mapping_key("images", str(source), config.remote_dir(config.sync.mappings[0]))
    assert state.get(key, relative)["status"] == Status.SUCCESS
    return config, backend, state, source, path, service, key, relative


def seed_legacy_success(config, backend, state, source, relative, payload=b"old verified image"):
    """Represent a pre-upgrade row without depending on new cache column names."""
    path = source / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    root = config.remote_dir(config.sync.mappings[0])
    remote = root + "/" + relative
    target = backend.target(remote)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    key = mapping_key("images", str(source), root)
    info = path.stat()
    state.update(key, relative, observed_size=info.st_size, observed_mtime=info.st_mtime_ns,
                 status=Status.SUCCESS, sha256=hashlib.sha256(payload).hexdigest(),
                 remote_version=backend.get_metadata(remote).version(
                     modtime=config.backend.reliable_modtime, hashes=config.backend.reliable_hashes),
                 last_success=1234)
    return path, key


@pytest.mark.skipif(os.name == "nt", reason="Windows upload mode conservatively keeps full content checks")
def test_unchanged_success_neither_rehashes_nor_rewrites_history(setup, monkeypatch):
    _, backend, state, _, path, service, key, relative = completed_upload(setup)
    before = state.get(key, relative)
    import cloudbridge.services.uploader as module
    monkeypatch.setattr(module, "sha256", lambda path: pytest.fail("不变的成功文件不应重新读取内容"))
    monkeypatch.setattr(state, "update", lambda *args, **kwargs: pytest.fail("不变的成功文件不应重写状态"))
    service.run_once()
    assert backend.uploads == 1
    assert state.get(key, relative) == before
    assert path.read_bytes() == b"version 0001"


def test_old_newly_discovered_file_still_requires_two_observations(setup):
    config, backend, state, source = setup
    config.sync.min_file_age_seconds = 120
    path = source / "already old.png"
    path.write_bytes(b"complete but not yet observed")
    old = time.time() - 600
    os.utime(path, (old, old))
    service = Uploader(config, backend, state)
    service.run_once()
    assert backend.uploads == 0
    service.run_once()
    assert backend.uploads == 1


def test_matching_observations_do_not_bypass_minimum_age(setup, monkeypatch):
    config, backend, state, source = setup
    config.sync.min_file_age_seconds = 120
    path = source / "fresh.png"
    path.write_bytes(b"fresh output")
    clock = [path.stat().st_mtime + 1]
    import cloudbridge.services.uploader as module
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    service = Uploader(config, backend, state)
    service.run_once()
    service.run_once()
    assert backend.uploads == 0
    clock[0] += 121
    service.run_once()
    assert backend.uploads == 1


@pytest.mark.skipif(os.name == "nt", reason="Linux ctime detects preserved-mtime content changes; native Windows semantics differ")
@pytest.mark.parametrize("replace_file", [False, True])
def test_same_size_preserved_mtime_edit_or_replacement_is_not_cached_forever(setup, replace_file):
    config, backend, state, _, path, service, key, relative = completed_upload(setup)
    original = path.stat()
    if replace_file:
        replacement = path.with_suffix(".tmp")
        replacement.write_bytes(b"version 0002")
        os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
        replacement.replace(path)
    else:
        path.write_bytes(b"version 0002")
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert path.stat().st_size == original.st_size
    assert path.stat().st_mtime_ns == original.st_mtime_ns
    assert (path.stat().st_ctime_ns, path.stat().st_ino) != (original.st_ctime_ns, original.st_ino)
    service.run_once()
    assert backend.uploads == 1
    assert state.get(key, relative)["status"] == Status.WAITING_STABLE
    service.run_once()
    assert backend.uploads == 2
    assert backend.target(config.remote_dir(config.sync.mappings[0]) + "/" + relative).read_bytes() == b"version 0002"
    assert state.get(key, relative)["status"] == Status.SUCCESS
    service.run_once()
    assert backend.uploads == 2


def test_force_still_hashes_and_transfers_selected_file(setup, monkeypatch):
    _, backend, _, _, path, service, _, _ = completed_upload(setup)
    import cloudbridge.services.uploader as module
    actual = module.sha256
    calls = []
    def digest(value):
        calls.append(value)
        return actual(value)
    monkeypatch.setattr(module, "sha256", digest)
    service.run_once(force=True)
    assert path in calls
    assert backend.uploads == 2


def test_missing_remote_is_repaired_without_force_even_when_local_fingerprint_unchanged(setup):
    config, backend, state, _, path, service, key, relative = completed_upload(setup)
    target = backend.target(config.remote_dir(config.sync.mappings[0]) + "/" + relative)
    target.unlink()
    service.run_once()
    assert target.read_bytes() == path.read_bytes()
    assert backend.uploads == 2
    assert state.get(key, relative)["status"] == Status.SUCCESS


def test_metadata_only_local_change_updates_cache_without_reupload(setup):
    _, backend, state, _, path, service, key, relative = completed_upload(setup)
    before = state.get(key, relative)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns - 1_000_000_000))
    service.run_once()
    service.run_once()
    assert backend.uploads == 1
    after = state.get(key, relative)
    assert after["status"] == Status.SUCCESS
    assert after["last_success"] == before["last_success"]
    assert after["hashed_fingerprint"] != before["hashed_fingerprint"]


@pytest.mark.skipif(os.name == "nt", reason="Linux upload hash-cache behavior")
def test_history_uses_bulk_metadata_and_confirms_missing_listing_entries(setup, monkeypatch):
    _, backend, state, _, _, service, key, relative = completed_upload(setup)
    import cloudbridge.services.uploader as module
    monkeypatch.setattr(module, "sha256", lambda path: pytest.fail("不变历史条目不应读取内容"))
    original_metadata = backend.get_metadata
    calls = []
    def metadata(remote):
        calls.append(remote)
        return original_metadata(remote)
    backend.get_metadata = metadata
    service.run_once()
    assert calls == []  # A normal bulk hit needs no per-file subprocess.
    backend.list_files = lambda remote: []  # Simulate a transient incomplete listing.
    service.run_once()
    assert len(calls) == 1 and backend.uploads == 1
    assert state.get(key, relative)["status"] == Status.SUCCESS


def test_missing_entire_remote_directory_is_repaired(setup):
    config, backend, state, _, path, service, key, relative = completed_upload(setup)
    from cloudbridge.backends.base import BackendError
    shutil.rmtree(backend.target(config.remote_dir(config.sync.mappings[0])))
    original = backend.list_files
    def missing(remote):
        if not backend.target(remote).exists():
            raise BackendError("controlled missing directory", code=3)
        return original(remote)
    backend.list_files = missing
    service.run_once()
    assert backend.uploads == 2
    assert backend.target(config.remote_dir(config.sync.mappings[0]) + "/" + relative).read_bytes() == path.read_bytes()


def test_source_changed_during_upload_never_records_success_and_can_recover(setup):
    config, backend, state, source = setup
    path = source / "changing.png"
    path.write_bytes(b"version 0001")
    service = Uploader(config, backend, state)
    service.run_once()
    original_upload = backend.upload
    def upload_then_change(local, remote):
        original_upload(local, remote)
        old = path.stat()
        path.write_bytes(b"version 0002")
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    backend.upload = upload_then_change
    service.run_once()
    key = mapping_key("images", str(source), config.remote_dir(config.sync.mappings[0]))
    assert state.get(key, "changing.png")["status"] != Status.SUCCESS
    backend.upload = original_upload
    service.run_once(force=True)
    service.run_once(force=True)
    assert state.get(key, "changing.png")["status"] == Status.SUCCESS
    assert backend.target(config.remote_dir(config.sync.mappings[0]) + "/changing.png").read_bytes() == b"version 0002"


@pytest.mark.skipif(os.name == "nt", reason="Uses Linux ctime to detect a write whose mtime was restored")
def test_content_mutated_while_hashing_is_not_sent_as_a_stable_snapshot(setup, monkeypatch):
    config, backend, state, source = setup
    path = source / "hash-race.png"
    path.write_bytes(b"version 0001")
    service = Uploader(config, backend, state)
    service.run_once()
    import cloudbridge.services.uploader as module
    original = module.sha256
    changed = []
    def digest_then_change(value):
        digest = original(value)
        if value == path and not changed:
            before = path.stat()
            path.write_bytes(b"version 0002")
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            changed.append(True)
        return digest
    monkeypatch.setattr(module, "sha256", digest_then_change)
    service.run_once()
    key = mapping_key("images", str(source), config.remote_dir(config.sync.mappings[0]))
    assert state.get(key, path.name)["status"] != Status.SUCCESS
    assert backend.uploads == 0


@pytest.mark.skipif(os.name == "nt", reason="Windows upload mode conservatively keeps full content checks")
def test_upgrade_legacy_success_only_seeds_once_without_reupload(setup, monkeypatch):
    config, backend, state, source = setup
    path, key = seed_legacy_success(config, backend, state, source, "legacy.png")
    service = Uploader(config, backend, state)
    service.run_once()
    service.run_once()
    assert backend.uploads == 0
    assert state.get(key, "legacy.png")["last_success"] == 1234
    import cloudbridge.services.uploader as module
    monkeypatch.setattr(module, "sha256", lambda value: pytest.fail("已建立的旧记录指纹不应反复重读"))
    service.run_once()
    assert backend.uploads == 0
    assert path.read_bytes() == b"old verified image"


def test_new_file_observation_precedes_legacy_content_audit(setup, monkeypatch):
    config, backend, state, source = setup
    config.sync.max_concurrent_transfers = 1
    for index in range(8):
        seed_legacy_success(config, backend, state, source, f"a legacy {index}.png")
    new = source / "zzz new output.png"
    new.write_bytes(b"new pending output")
    key = mapping_key("images", str(source), config.remote_dir(config.sync.mappings[0]))
    import cloudbridge.services.uploader as module
    original = module.sha256
    def inspect_after_new_observation(path):
        if path.name.startswith("a legacy"):
            row = state.get(key, new.name)
            assert row and row["status"] in (Status.WAITING_STABLE, Status.SUCCESS), "观察新文件不能排在历史内容校验之后"
        return original(path)
    monkeypatch.setattr(module, "sha256", inspect_after_new_observation)
    service = Uploader(config, backend, state)
    service.run_once()
    assert state.get(key, new.name)["status"] == Status.WAITING_STABLE
    assert backend.uploads == 0
    service.run_once()
    assert backend.uploads == 1
    assert backend.target(config.remote_dir(config.sync.mappings[0]) + "/" + new.name).read_bytes() == new.read_bytes()


def test_ready_new_file_upload_precedes_legacy_hash_seeding(setup, monkeypatch):
    config, backend, state, source = setup
    config.sync.max_concurrent_transfers = 1
    for index in range(4):
        seed_legacy_success(config, backend, state, source, f"a legacy {index}.png")
    new = source / "zzz ready.png"
    new.write_bytes(b"new work before historical audit")
    service = Uploader(config, backend, state)
    # First cycle collects observations; new files are still ineligible.
    service.run_once()
    target = backend.target(config.remote_dir(config.sync.mappings[0]) + "/" + new.name)
    import cloudbridge.services.uploader as module
    original = module.sha256
    def legacy_after_upload(path):
        if path.name.startswith("a legacy"):
            assert target.is_file(), "已稳定的新文件应先于历史种子哈希校验上传"
        return original(path)
    monkeypatch.setattr(module, "sha256", legacy_after_upload)
    service.run_once()
    assert target.read_bytes() == new.read_bytes()
    assert backend.uploads == 1
