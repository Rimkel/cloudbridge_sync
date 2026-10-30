import json
from pathlib import Path, PureWindowsPath
import shlex
import sys
import time

import pytest

from cloudbridge.config import save_config
from cloudbridge.launcher.console import clean, format_side
from cloudbridge.launcher.profile import Peer, Profile, decode_peer, encode_peer, load_profile, ssh_command
from cloudbridge.launcher.snapshot import snapshot
from cloudbridge.launcher.worker import atomic_json, prepare, run_worker, stop_peer


def peer_for(setup, tmp_path):
    config, _, _, _ = setup
    path = tmp_path / "配置 with spaces.yaml"
    save_config(path, config)
    return Peer(project_dir=str(tmp_path), sync_config=str(path), mode="upload")


def test_peer_roundtrip_and_shell_quoting(tmp_path):
    peer = Peer(project_dir="/home/test/中文 dir'$(touch /tmp/nope)", sync_config="configs/ubuntu.yaml", mode="upload")
    assert decode_peer(encode_peer(peer)) == peer
    profile = Profile(local=peer, remote=peer)
    args = ssh_command(profile, "watch")
    decoded = shlex.split(args[-1])
    assert decoded[0] == peer.project_dir + "/.venv/bin/python"
    assert decode_peer(decoded[decoded.index("--peer") + 1]) == peer
    for host in ("-oProxyCommand=evil", "gpu-server;id", "bad host", "$(bad)"):
        with pytest.raises(ValueError):
            Profile(local=peer, remote=peer, ssh_host=host)


@pytest.mark.parametrize("action", ["check", "start", "watch", "stop"])
@pytest.mark.parametrize("project_dir", ["/home/test/cloudbridge", "/home/test/中文 space'$(not-a-command)/"])
def test_ssh_ubuntu_path_ignores_windows_controller_path_flavour(monkeypatch, action, project_dir):
    """Reproduce Windows path formatting without claiming a native Windows run."""
    import cloudbridge.launcher.profile as profile_module
    peer = Peer(project_dir=project_dir, sync_config="configs/ubuntu.yaml", mode="upload")
    profile = Profile(local=peer, remote=peer)
    # The original code's Path(...) call becomes WindowsPath on the user's PC.
    # Isolate that path flavour here; do not change os.name or the real filesystem.
    monkeypatch.setattr(profile_module, "Path", PureWindowsPath)
    args = ssh_command(profile, action)
    command = shlex.split(args[-1])
    assert command[0] == project_dir.rstrip("/") + "/.venv/bin/python"
    assert "\\" not in command[0]
    assert command[1:5] == ["-m", "cloudbridge.launcher", "agent", action]
    assert decode_peer(command[command.index("--peer") + 1]) == peer


@pytest.mark.parametrize("url", ["https://outside.example", "http://user:password@127.0.0.1:5244", "http://127.0.0.1/?token=x"])
def test_gateway_settings_reject_remote_or_credentials(url):
    with pytest.raises(ValueError):
        Peer(project_dir="/tmp", sync_config="x", mode="upload", openlist_url=url)


def test_profile_relative_root(tmp_path):
    configs = tmp_path / "中文 configs"
    configs.mkdir()
    profile = configs / "oneclick.yaml"
    profile.write_text("""ssh_host: gpu-server
local:
  project_dir: ..
  sync_config: configs/windows.yaml
  mode: download
remote:
  project_dir: /remote/Cloud Bridge
  sync_config: configs/ubuntu.yaml
  mode: upload
""", encoding="utf-8")
    loaded = load_profile(profile)
    assert loaded.local.project_dir == str(tmp_path)
    assert loaded.local.config_path == tmp_path / "configs/windows.yaml"


def test_prepare_refuses_empty_gateway_without_initializing(setup, tmp_path):
    peer = peer_for(setup, tmp_path)
    peer.openlist_data_dir = "empty data"
    peer.openlist_executable = sys.executable
    with pytest.raises(ValueError, match="拒绝初始化"):
        prepare(peer)
    assert not (tmp_path / "empty data").exists()


def test_prepare_pins_original_gateway_and_caches_discovery(setup, tmp_path, monkeypatch):
    peer = peer_for(setup, tmp_path)
    data = tmp_path / "原有 OpenList data"
    data.mkdir()
    (data / "data.db").write_bytes(b"preserve-me")
    (data / "config.json").write_text("{}")
    import cloudbridge.launcher.worker as worker
    monkeypatch.setattr(worker, "discover_openlist", lambda: (sys.executable, str(data), str(data / "config.json")))
    prepared = prepare(peer)
    atomic_json(peer.runtime_dir / "peer.json", prepared.model_dump())
    monkeypatch.setattr(worker, "discover_openlist", lambda: None)
    assert prepare(peer) == prepared
    assert (data / "data.db").read_bytes() == b"preserve-me"


def test_gateway_supervisor_passes_fixed_paths_without_shell(tmp_path, monkeypatch):
    from cloudbridge.launcher.worker import supervise_openlist
    import cloudbridge.launcher.worker as worker
    peer = Peer(project_dir=str(tmp_path), sync_config="config.yaml", mode="upload",
                openlist_executable="/tools/OpenList space/openlist", openlist_data_dir="/original/中文 data",
                openlist_config_file="/original/中文 data/config.json")
    captured = {}
    class Child:
        terminated = False
        def poll(self):
            return None
        def terminate(self):
            self.terminated = True
        def wait(self, timeout):
            return 0
    child = Child()
    class Stop:
        stopped = False
        def is_set(self):
            return self.stopped
        def wait(self, seconds):
            self.stopped = True
    def popen(args, **kwargs):
        captured.update(args=args, **kwargs)
        return child
    monkeypatch.setattr(worker, "listening", lambda peer: False)
    monkeypatch.setattr(worker.subprocess, "Popen", popen)
    assert supervise_openlist(peer, Stop(), {}) == 0
    assert captured["args"] == [peer.openlist_executable, "server", "--data", peer.openlist_data_dir,
                                "--config", peer.openlist_config_file]
    assert captured["cwd"] == "/original"
    assert captured["shell"] is False and child.terminated


def test_existing_foreground_instance_is_not_replaced(setup, tmp_path, monkeypatch):
    from cloudbridge.core.locking import InstanceLock
    import cloudbridge.launcher.worker as worker
    peer = peer_for(setup, tmp_path)
    monkeypatch.setattr(worker, "prepare", lambda peer: peer)
    monkeypatch.setattr(worker, "spawn", lambda *args: pytest.fail("不应启动或停止其他实例"))
    with InstanceLock(Path(str(peer.config_path) + ".lock")):
        with pytest.raises(ValueError, match="已有实例"):
            worker.start_peer(peer)


def test_gateway_data_inside_transfer_tree_is_rejected(setup, tmp_path):
    peer = peer_for(setup, tmp_path)
    config, _, _, source = setup
    peer.openlist_data_dir = str(source / "private-openlist")
    with pytest.raises(ValueError, match="不能位于同步目录"):
        prepare(peer)


def test_snapshot_is_read_only_and_handles_stale_workers(setup, tmp_path):
    peer = peer_for(setup, tmp_path)
    config, _, state, _ = setup
    state.update("images", "当前.png", status="TRANSFERRING", observed_size=100)
    state.update("images", "done.png", status="SUCCESS", last_success=123)
    atomic_json(peer.runtime_dir / "transfer.json", {"state": "running", "updated": time.time() - 30})
    result = snapshot(peer)
    assert result["counts"] == {"SUCCESS": 1, "TRANSFERRING": 1}
    assert result["active"] == ["当前.png"]
    assert result["transfer"]["state"] == "unknown"
    assert state.get("images", "当前.png")["status"] == "TRANSFERRING"
    config.state.db_path = str(tmp_path / "never-created.db")
    save_config(peer.config_path, config)
    assert snapshot(peer)["counts"] == {}
    assert not Path(config.state.db_path).exists()


def test_console_never_invents_total_percentage():
    text = format_side("上传", {"counts": {"SUCCESS": 3, "WAITING_STABLE": 2, "TRANSFERRING": 1},
                              "active": ["bad\x1b[2J\nimage.png"], "transfer": {"state": "running"}})
    assert "已记录成功 3" in text and "等待 2" in text
    assert "%" not in text and "\x1b" not in text
    assert clean("a\nb\x00") == "ab"


def test_stop_nonce_and_never_kills_unowned_process(setup, tmp_path):
    peer = peer_for(setup, tmp_path)
    atomic_json(peer.runtime_dir / "transfer.json", {"state": "running", "nonce": "current", "updated": time.time()})
    assert not stop_peer(peer, timeout=0)
    request = json.loads((peer.runtime_dir / "transfer.stop.json").read_text())
    assert request == {"nonce": "current"}
    # A stale heartbeat is not proof a process stopped.
    atomic_json(peer.runtime_dir / "transfer.json", {"state": "running", "nonce": "stale", "updated": 1})
    assert not stop_peer(peer, timeout=0)


def test_worker_stop_event_and_failed_exit_are_persisted(setup, tmp_path, monkeypatch):
    peer = peer_for(setup, tmp_path)
    import cloudbridge.core.scheduler as scheduler
    observed = []
    def run(config_path, mode, *, daemon, force, stop_event):
        observed.append((config_path, mode, daemon, force))
        record = json.loads((peer.runtime_dir / "transfer.json").read_text())
        atomic_json(peer.runtime_dir / "transfer.stop.json", {"nonce": record["nonce"]})
        assert stop_event.wait(3)
        return 0
    monkeypatch.setattr(scheduler, "run", run)
    assert run_worker(peer, "transfer") == 0
    assert observed == [(peer.config_path, "upload", True, False)]
    assert json.loads((peer.runtime_dir / "transfer.json").read_text())["state"] == "stopped"
    monkeypatch.setattr(scheduler, "run", lambda *args, **kwargs: 1)
    assert run_worker(peer, "transfer") == 1
    assert json.loads((peer.runtime_dir / "transfer.json").read_text())["state"] == "failed"


def test_rclone_has_no_visible_windows_console(monkeypatch):
    from cloudbridge.utils.subprocess import CommandRunner
    import cloudbridge.utils.subprocess as runner
    captured = {}
    class Process:
        returncode = 0
        def poll(self):
            return 0
        def communicate(self, timeout):
            return "ok", ""
    monkeypatch.setattr(runner.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    # Avoid modifying os.name globally: pathlib selects its concrete type from it.
    from types import SimpleNamespace
    monkeypatch.setattr(runner, "os", SimpleNamespace(name="nt"))
    def popen(args, **kwargs):
        captured.update(kwargs)
        return Process()
    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    assert CommandRunner(2).run(["rclone", "version"]) == "ok"
    assert captured["creationflags"] == 0x08000000
