import pytest

from cloudbridge.core.locking import InstanceLock
from cloudbridge.core.models import Status
from cloudbridge.core.state import StateStore


def test_persistence_recovery_claim_and_binding(tmp_path):
    path = tmp_path / "db"
    with StateStore(path) as state:
        state.bind({"mode": "upload"})
        state.update("m", "x", status=Status.PENDING)
        assert state.claim("m", "x")
        assert not state.claim("m", "x")
    with StateStore(path) as state:
        state.recover()
        assert state.get("m", "x")["status"] == Status.RETRY
        with pytest.raises(ValueError, match="绑定"):
            state.bind({"mode": "download"})


def test_os_lock_and_release(tmp_path):
    path = tmp_path / "instance.lock"
    with InstanceLock(path):
        with pytest.raises(ValueError, match="已有实例"):
            with InstanceLock(path):
                pass
    with InstanceLock(path):
        pass


def test_recent_failure_uses_failure_time_not_file_insertion_order(tmp_path):
    with StateStore(tmp_path / "state.db") as state:
        state.update("m", "old", status=Status.FAILED, last_error="first", last_failure=10)
        state.update("m", "new", status=Status.FAILED, last_error="second", last_failure=20)
        state.update("m", "old", status=Status.FAILED, last_error="latest", last_failure=30)
        assert state.summary()["last_error"] == "latest"
