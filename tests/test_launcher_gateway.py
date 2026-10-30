import io
import json
from types import SimpleNamespace

import pytest

from cloudbridge.config import save_config
from cloudbridge.launcher.gateway import check_external_gateway, system_openlist_running
from cloudbridge.launcher.profile import Peer
from cloudbridge.launcher.snapshot import snapshot
from cloudbridge.launcher.worker import atomic_json, prepare, start_peer


def make_peer(setup, tmp_path):
    config, _, _, _ = setup
    path = tmp_path / "config.yaml"
    save_config(path, config)
    return Peer(project_dir=str(tmp_path), sync_config=str(path), mode="upload")


def test_auto_reuses_system_openlist_without_accessing_other_users_data(setup, tmp_path, monkeypatch):
    peer = make_peer(setup, tmp_path)
    import cloudbridge.launcher.worker as worker
    calls = []
    monkeypatch.setattr(worker, "discover_openlist", lambda: None)
    monkeypatch.setattr(worker, "system_openlist_running", lambda service: service == "openlist.service")
    monkeypatch.setattr(worker, "check_external_gateway", lambda peer: calls.append(peer.openlist_url))
    prepared = prepare(peer)
    assert prepared.openlist_management == "external"
    assert prepared.openlist_data_dir == ""
    assert calls == ["http://127.0.0.1:5244"]
    assert peer.openlist_management == "auto"
    assert not peer.runtime_dir.exists()


def test_managed_mode_requires_own_data_even_if_system_service_exists(setup, tmp_path, monkeypatch):
    peer = make_peer(setup, tmp_path)
    peer.openlist_management = "managed"
    import cloudbridge.launcher.worker as worker
    monkeypatch.setattr(worker, "discover_openlist", lambda: None)
    monkeypatch.setattr(worker, "system_openlist_running", lambda service: pytest.fail("不应回退到外部服务"))
    with pytest.raises(ValueError, match="未找到 Ubuntu 原有"):
        prepare(peer)


def test_cached_external_gateway_is_checked_again(setup, tmp_path, monkeypatch):
    peer = make_peer(setup, tmp_path)
    previous = peer.model_copy(update={"openlist_management": "external", "openlist_data_dir": "/private/no-permission"})
    atomic_json(peer.runtime_dir / "peer.json", previous.model_dump())
    import cloudbridge.launcher.worker as worker
    monkeypatch.setattr(worker, "discover_openlist", lambda: pytest.fail("应使用缓存的外部模式"))
    monkeypatch.setattr(worker, "check_external_gateway", lambda peer: "test")
    assert prepare(peer).openlist_management == "external"
    def offline(peer):
        raise ValueError("外部网关未就绪")
    monkeypatch.setattr(worker, "check_external_gateway", offline)
    with pytest.raises(ValueError, match="未就绪"):
        prepare(peer)


def test_external_start_spawns_transfer_only_and_snapshot_reports_service(setup, tmp_path, monkeypatch):
    peer = make_peer(setup, tmp_path)
    peer.openlist_management = "external"
    import cloudbridge.launcher.worker as worker
    monkeypatch.setattr(worker, "check_external_gateway", lambda peer: "test")
    roles = []
    def spawn(prepared, role):
        roles.append(role)
        import time
        atomic_json(peer.runtime_dir / "transfer.json", {"state": "running", "updated": time.time()})
    monkeypatch.setattr(worker, "spawn", spawn)
    assert start_peer(peer).openlist_management == "external"
    assert roles == ["transfer"]
    assert snapshot(peer)["openlist"]["state"] == "external"
    assert not (peer.runtime_dir / "openlist.json").exists()


def test_external_mode_still_protects_runtime_metadata_from_sync(setup, tmp_path):
    config, _, _, source = setup
    path = source / "config.yaml"
    save_config(path, config)
    peer = Peer(project_dir=str(tmp_path), sync_config=str(path), mode="upload", openlist_management="external")
    with pytest.raises(ValueError, match="启动器状态不能位于同步目录内"):
        prepare(peer)


@pytest.mark.parametrize("active,name,expected", [(True, "openlist", True), (False, "openlist", False), (True, "other-app", False)])
def test_systemd_detection_requires_active_openlist_process(tmp_path, monkeypatch, active, name, expected):
    import cloudbridge.launcher.gateway as gateway
    monkeypatch.setattr(gateway.sys, "platform", "linux")
    proc = tmp_path / "proc/1234"
    proc.mkdir(parents=True)
    (proc / "comm").write_text(name)
    monkeypatch.setattr(gateway, "Path", lambda value: tmp_path / "proc")
    def run(args, **kwargs):
        assert args == ["systemctl", "show", "openlist.service", "--property=ActiveState,MainPID"]
        assert kwargs["shell"] is False
        return SimpleNamespace(returncode=0, stdout=f"ActiveState={'active' if active else 'inactive'}\nMainPID=1234\n")
    monkeypatch.setattr(gateway.subprocess, "run", run)
    assert system_openlist_running("openlist.service") == expected


@pytest.mark.parametrize("body,valid", [
    ({"code": 200, "data": {"version": "v4.2.7"}}, True),
    ({"code": 401, "message": "private-response"}, False),
    ({"code": 200, "data": {}}, False),
    (["invalid"], False),
])
def test_public_health_check_validates_api_and_hides_responses(monkeypatch, body, valid):
    import cloudbridge.launcher.gateway as gateway
    peer = Peer(project_dir="/tmp", sync_config="config", mode="upload", openlist_management="external")
    class Response(io.BytesIO):
        status = 200
    def open_request(url, timeout):
        assert url == "http://127.0.0.1:5244/api/public/settings"
        return Response(json.dumps(body).encode())
    monkeypatch.setattr(gateway.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    if valid:
        assert check_external_gateway(peer) == "v4.2.7"
    else:
        with pytest.raises(ValueError, match="外部 OpenList 网关未就绪") as error:
            check_external_gateway(peer)
        assert "private-response" not in str(error.value)


def test_invalid_service_name_rejected():
    with pytest.raises(ValueError):
        Peer(project_dir="/tmp", sync_config="x", mode="upload", openlist_service="--other-option")
