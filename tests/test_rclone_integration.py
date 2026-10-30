"""Actual rclone over loopback WebDAV. No OpenList/Baidu/Windows claims."""
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from cloudbridge.backends.rclone_webdav import RcloneWebDAVBackend
from cloudbridge.config import Config, save_config
from cloudbridge.core.planner import sha256
from cloudbridge.core.state import StateStore
from cloudbridge.services.downloader import Downloader
from cloudbridge.services.uploader import Uploader


@pytest.fixture
def real_webdav(tmp_path, monkeypatch):
    executable = os.environ.get("CLOUDBRIDGE_TEST_RCLONE") or shutil.which("rclone")
    if not executable:
        pytest.skip("需要 rclone；设置 CLOUDBRIDGE_TEST_RCLONE 以启用真实本地 WebDAV 测试")
    root = tmp_path / "webdav"
    (root / "relay/Images").mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    private_config = tmp_path / "rclone.conf"
    private_config.write_text(f"[localdav]\ntype = webdav\nurl = http://127.0.0.1:{port}/\nvendor = other\n", encoding="utf-8")
    monkeypatch.setenv("RCLONE_CONFIG", str(private_config))
    # Keep these tests independent of user rclone option environment settings.
    for key in list(os.environ):
        if key.startswith("RCLONE_") and key != "RCLONE_CONFIG":
            monkeypatch.delenv(key)
    process = subprocess.Popen([executable, "serve", "webdav", str(root), "--addr", f"127.0.0.1:{port}",
                                "--log-level", "ERROR"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=.2):
                    break
            except OSError:
                if process.poll() is not None or time.monotonic() > deadline:
                    pytest.fail("本地 WebDAV 服务未能启动")
                threading.Event().wait(.05)
        yield executable, root
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def test_actual_rclone_webdav_roundtrip_and_cli(real_webdav, tmp_path, monkeypatch):
    executable, root = real_webdav
    source = tmp_path / "中文 source with space"
    source.mkdir()
    (source / "nested").mkdir()
    path = source / "nested/中文 image.png"
    path.write_bytes(bytes(range(256)) * 4096)
    config = Config.model_validate(dict(
        backend=dict(rclone_remote="localdav", cloud_root="/relay", executable=executable, timeout_seconds=15),
        sync=dict(min_file_age_seconds=0, mappings=[dict(name="images", local_dir=str(source), remote_dir="Images")]),
        state=dict(db_path=str(tmp_path / "upload.db")), logging=dict(file=str(tmp_path / "upload.log"))))
    backend = RcloneWebDAVBackend(config.backend, "upload")
    backend.check_connection()
    assert backend.get_metadata("/relay/missing.png") is None
    with StateStore(Path(config.state.db_path)) as state:
        service = Uploader(config, backend, state)
        service.run_once()
        service.run_once()
        assert state.summary()["counts"] == {"SUCCESS": 1}
        assert sha256(path) == sha256(root / "relay/Images/nested/中文 image.png")
        service.run_once()
        assert state.summary()["counts"] == {"SUCCESS": 1}
    destination = tmp_path / "download 中文 space"
    destination.mkdir()
    config.app.mode = "download"
    config.sync.mappings[0].local_dir = str(destination)
    config.state.db_path = str(tmp_path / "download.db")
    with StateStore(Path(config.state.db_path)) as state:
        download = Downloader(config, RcloneWebDAVBackend(config.backend, "download"), state)
        download.run_once()
        download.run_once()
        assert state.summary()["counts"] == {"SUCCESS": 1}
    assert sha256(path) == sha256(destination / "nested/中文 image.png")
    config_path = tmp_path / "client.yaml"
    save_config(config_path, config)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from cloudbridge.cli import main
    assert main(["--config", str(config_path), "doctor"]) == 0
    assert main(["--config", str(config_path), "download", "--once"]) == 0


def test_real_rclone_parallel_downloads_keep_all_sha256_values(real_webdav, tmp_path):
    """Six real WebDAV copies in two concurrent batches, not a Baidu/Windows test."""
    executable, root = real_webdav
    destination = tmp_path / "并发 download with spaces"
    destination.mkdir()
    expected = {}
    for index in range(6):
        relative = f"共同 folder {index % 2}/中文 file {index}.png"
        remote = root / "relay/Images" / relative
        remote.parent.mkdir(parents=True, exist_ok=True)
        remote.write_bytes(bytes([index]) * (1024 * 1024) + f"file-{index}".encode())
        expected[relative] = sha256(remote)
    config = Config.model_validate(dict(
        app=dict(mode="download"),
        backend=dict(rclone_remote="localdav", cloud_root="/relay", executable=executable, timeout_seconds=20),
        sync=dict(max_concurrent_transfers=3,
                  mappings=[dict(name="images", local_dir=str(destination), remote_dir="Images")]),
        state=dict(db_path=str(tmp_path / "parallel.db")), logging=dict(file=str(tmp_path / "parallel.log"))))
    backend = RcloneWebDAVBackend(config.backend, "download")
    original = backend.download
    barrier = threading.Barrier(3)
    lock = threading.Lock()
    active = peak = 0
    requests = []
    def download(remote, stage):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            requests.append(remote)
        try:
            barrier.wait(timeout=10)
            original(remote, stage)
        finally:
            with lock:
                active -= 1
    backend.download = download
    with StateStore(Path(config.state.db_path)) as state:
        service = Downloader(config, backend, state)
        service.run_once()
        assert not list(destination.rglob("*.png"))
        service.run_once()
        assert state.summary()["counts"] == {"SUCCESS": 6}
        assert peak == 3 and len(requests) == len(set(requests)) == 6
        for relative, digest in expected.items():
            assert sha256(destination / relative) == digest
        assert not list(destination.rglob(".cloudbridge-*"))
        service.run_once()
        assert len(requests) == 6


@pytest.mark.skipif(os.name == "nt", reason="此测试验证 Linux 分离进程；Windows 需用户环境验收")
def test_launcher_detached_real_upload_duplicate_and_stop(real_webdav, tmp_path, monkeypatch):
    """Actual detached worker + rclone + local WebDAV; no Baidu account used."""
    import json
    import sys
    from cloudbridge.launcher.profile import Peer, encode_peer
    from cloudbridge.launcher.snapshot import read_json, snapshot
    from cloudbridge.launcher.worker import atomic_json, start_peer, stop_peer

    executable, root = real_webdav
    monkeypatch.setenv("HOME", str(tmp_path))  # No user instance locks/routes modified.
    source = tmp_path / "source 中文 space"
    source.mkdir()
    image = source / "test.png"
    image.write_bytes(bytes(range(256)) * 4096)
    config = Config.model_validate(dict(
        backend=dict(rclone_remote="localdav", cloud_root="/relay", executable=str(Path(executable).resolve()), timeout_seconds=10),
        sync=dict(interval_seconds=.1, min_file_age_seconds=0,
                  mappings=[dict(name="images", local_dir=str(source), remote_dir="Images")]),
        state=dict(db_path=str(tmp_path / "upload.db")), logging=dict(file=str(tmp_path / "upload.log"))))
    config_path = tmp_path / "launcher config.yaml"
    save_config(config_path, config)
    fake_data = tmp_path / "original-data"
    fake_data.mkdir()
    (fake_data / "data.db").write_bytes(b"preserved-existing-data")
    (fake_data / "config.json").write_text("{}")
    private_config = Path(os.environ["RCLONE_CONFIG"]).read_text()
    url = next(line.split("=", 1)[1].strip() for line in private_config.splitlines() if line.startswith("url"))
    peer = Peer(project_dir=str(Path(__file__).resolve().parents[1]), sync_config=str(config_path), mode="upload",
                openlist_executable=sys.executable, openlist_data_dir=str(fake_data), openlist_url=url)
    def wait_for(predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.1)
        pytest.fail("后台本地测试等待超时")
    try:
        start_peer(peer)  # Reuses existing test WebDAV, never runs a real OpenList.
        wait_for(lambda: snapshot(peer)["counts"].get("SUCCESS") == 1)
        pid = read_json(peer.runtime_dir / "transfer.json")["pid"]
        start_peer(peer)
        assert read_json(peer.runtime_dir / "transfer.json")["pid"] == pid
        # Closing the status stream must not close the detached worker.
        preview = subprocess.Popen([sys.executable, "-m", "cloudbridge.launcher", "agent", "watch",
                                    "--peer", encode_peer(peer), "--interval", "1"], stdout=subprocess.PIPE, text=True)
        assert json.loads(preview.stdout.readline())["counts"]["SUCCESS"] == 1
        preview.terminate()
        preview.wait(timeout=5)
        assert snapshot(peer)["transfer"]["state"] == "running"
        assert sha256(image) == sha256(root / "relay/Images/test.png")
        assert stop_peer(peer)
        assert snapshot(peer)["transfer"]["state"] == "stopped"
        with urllib.request.urlopen(url, timeout=1) as response:
            assert response.status == 200  # Stop never closes the reused gateway.
        assert image.exists() and (root / "relay/Images/test.png").exists()
        assert (fake_data / "data.db").read_bytes() == b"preserved-existing-data"
        # A new worker ignores the stop request addressed to the previous nonce.
        (source / "after-restart.png").write_bytes(b"new after restart")
        start_peer(peer)
        wait_for(lambda: snapshot(peer)["counts"].get("SUCCESS") == 2)
        assert read_json(peer.runtime_dir / "transfer.json")["pid"] != pid
        assert sha256(source / "after-restart.png") == sha256(root / "relay/Images/after-restart.png")
        assert stop_peer(peer)
    finally:
        stop_peer(peer)
        info = read_json(peer.runtime_dir / "openlist.json")
        if info.get("nonce"):
            atomic_json(peer.runtime_dir / "openlist.stop.json", {"nonce": info["nonce"]})
            wait_for(lambda: read_json(peer.runtime_dir / "openlist.json").get("state") == "stopped", 5)
