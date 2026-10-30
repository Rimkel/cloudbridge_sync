"""Explicit failure recovery must preserve successful files and local history."""
from pathlib import Path
import sqlite3

import pytest

from cloudbridge.cli import main
from cloudbridge.config import Mapping, save_config
from cloudbridge.core.locking import InstanceLock
from cloudbridge.core.models import Status
from cloudbridge.core.planner import mapping_key
from cloudbridge.core.scheduler import identity
from cloudbridge.services.downloader import Downloader


def configured(setup, tmp_path):
    config, backend, state, source = setup
    config.app.mode = "download"
    config.sync.mappings.extend([
        Mapping(name="second", local_dir=str(tmp_path / "second"), remote_dir="Second"),
        Mapping(name="disabled", enabled=False, local_dir=str(tmp_path / "disabled"), remote_dir="Disabled"),
    ])
    path = tmp_path / "config.yaml"
    save_config(path, config)
    keys = {mapping.name: mapping_key(mapping.name, mapping.local_dir, config.remote_dir(mapping))
            for mapping in config.sync.mappings}
    return config, backend, state, source, path, keys


def test_requeue_preserves_integrity_and_failure_history(setup):
    _, _, state, _ = setup
    state.update("selected", "failed.png", status=Status.FAILED, attempts=5, next_retry=999,
                 sha256="old-local-sha", remote_version="old-remote-version", last_success=100,
                 observed_size=12, observed_mtime=34, last_error="controlled failure", last_failure=200)
    before = state.get("selected", "failed.png")
    for name, status, permanent in (("done.png", Status.SUCCESS, 0), ("conflict.png", Status.CONFLICT, 0),
                                    ("active.png", Status.TRANSFERRING, 0), ("permanent.png", Status.FAILED, 1)):
        state.update("selected", name, status=status, permanent=permanent, attempts=5)
    state.update("other", "failed.png", status=Status.FAILED, attempts=5)
    protected = {(mapping, name): state.get(mapping, name) for mapping, name in (
        ("selected", "done.png"), ("selected", "conflict.png"), ("selected", "active.png"),
        ("selected", "permanent.png"), ("other", "failed.png"))}
    assert state.retry_failed(["selected", "selected"]) == 1
    assert state.get("selected", "failed.png") == {**before, "status": Status.RETRY, "attempts": 0, "next_retry": 0}
    for (mapping, name), previous in protected.items():
        assert state.get(mapping, name) == previous
    assert state.retry_failed(["selected"]) == 0


def test_requeue_multiple_mappings_is_atomic(setup):
    _, _, state, _ = setup
    state.update("first", "x", status=Status.FAILED, attempts=5)
    state.update("second", "x", status=Status.FAILED, attempts=5)
    state.db.execute("""CREATE TRIGGER reject_second BEFORE UPDATE ON files
                     WHEN NEW.mapping='second' BEGIN
                     SELECT RAISE(ABORT, 'controlled transaction failure'); END""")
    state.db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        state.retry_failed(["first", "second"])
    assert state.get("first", "x")["status"] == Status.FAILED
    assert state.get("second", "x")["status"] == Status.FAILED


def test_cli_default_only_requeues_active_current_mapping_keys(setup, tmp_path, capsys):
    config, _, state, _, path, keys = configured(setup, tmp_path)
    for key in [*keys.values(), "images:old-destination"]:
        state.update(key, "failed.png", status=Status.FAILED, attempts=5)
    state.update(keys["images"], "done.png", status=Status.SUCCESS, sha256="verified", last_success=123)
    successful = state.get(keys["images"], "done.png")
    assert main(["--config", str(path), "retry-failed"]) == 0
    assert "已恢复 2 个临时失败任务" in capsys.readouterr().out
    assert state.get(keys["images"], "failed.png")["status"] == Status.RETRY
    assert state.get(keys["second"], "failed.png")["status"] == Status.RETRY
    assert state.get(keys["disabled"], "failed.png")["status"] == Status.FAILED
    assert state.get("images:old-destination", "failed.png")["status"] == Status.FAILED
    assert state.get(keys["images"], "done.png") == successful


def test_cli_mapping_selection_and_bad_or_disabled_mapping(setup, tmp_path):
    _, _, state, _, path, keys = configured(setup, tmp_path)
    for key in keys.values():
        state.update(key, "x", status=Status.FAILED, attempts=5)
    assert main(["--config", str(path), "retry-failed", "--mapping", "second"]) == 0
    assert state.get(keys["second"], "x")["status"] == Status.RETRY
    assert state.get(keys["images"], "x")["status"] == Status.FAILED
    assert main(["--config", str(path), "retry-failed", "--mapping", "unknown"]) == 1
    assert main(["--config", str(path), "retry-failed", "--mapping", "disabled"]) == 1
    assert state.get(keys["disabled"], "x")["status"] == Status.FAILED


@pytest.mark.parametrize("which_lock", ["config", "db", "edit"])
def test_cli_rejects_running_or_editing_instance(setup, tmp_path, which_lock, capsys):
    config, _, state, _, path, keys = configured(setup, tmp_path)
    state.update(keys["images"], "x", status=Status.FAILED, attempts=5)
    lock_path = {"config": Path(str(path) + ".lock"),
                 "db": Path(config.state.db_path + ".lock"),
                 "edit": Path(str(path) + ".edit.lock")}[which_lock]
    with InstanceLock(lock_path):
        assert main(["--config", str(path), "retry-failed"]) == 1
    assert "已有实例" in capsys.readouterr().err
    assert state.get(keys["images"], "x")["status"] == Status.FAILED


def test_cli_rejects_state_bound_to_other_direction(setup, tmp_path):
    config, _, state, _, path, keys = configured(setup, tmp_path)
    state.bind({**identity(config), "mode": "upload"})
    state.update(keys["images"], "x", status=Status.FAILED, attempts=5)
    assert main(["--config", str(path), "retry-failed"]) == 1
    assert state.get(keys["images"], "x")["status"] == Status.FAILED


def test_cli_does_not_create_missing_state_database(setup, tmp_path, capsys):
    config, _, _, _, path, _ = configured(setup, tmp_path)
    missing = tmp_path / "missing.db"
    config.state.db_path = str(missing)
    save_config(path, config)
    assert main(["--config", str(path), "retry-failed"]) == 0
    assert "已恢复 0 个临时失败任务" in capsys.readouterr().out
    assert not missing.exists()


def test_recovery_downloads_only_failed_file_and_does_not_force_successes(setup, tmp_path):
    config, backend, state, source = setup
    config.app.mode = "download"
    config.backend.reliable_hashes = True
    root = config.remote_dir(config.sync.mappings[0])
    for name in ("failed.png", "done.png"):
        remote = backend.target(root + "/" + name)
        remote.parent.mkdir(parents=True, exist_ok=True)
        remote.write_bytes(name.encode())
    path = tmp_path / "config.yaml"
    save_config(path, config)
    service = Downloader(config, backend, state)
    service.run_once()
    original = backend.download
    calls = []
    from cloudbridge.backends.base import BackendError
    # Force one predictable failure independent of worker execution order.
    failed_once = False
    def first_failure(remote, stage):
        nonlocal failed_once
        calls.append(remote)
        if remote.endswith("failed.png") and not failed_once:
            failed_once = True
            raise BackendError("controlled download failure")
        original(remote, stage)
    backend.download = first_failure
    service.run_once()
    assert state.summary()["counts"] == {"FAILED": 1, "SUCCESS": 1}
    key = mapping_key("images", str(source), root)
    success_before = state.get(key, "done.png")
    assert main(["--config", str(path), "retry-failed"]) == 0
    assert state.get(key, "done.png") == success_before
    service.run_once()
    assert state.summary()["counts"] == {"SUCCESS": 2}
    assert sum(remote.endswith("done.png") for remote in calls) == 1
    assert sum(remote.endswith("failed.png") for remote in calls) == 2
