from pathlib import Path

import pytest

from cloudbridge.config import Config, load_config, save_config
from cloudbridge.core.routes import register_routes
from cloudbridge.core.scheduler import run


def test_cross_config_opposite_mode_overlap_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    source = tmp_path / "images"
    cfg = Config.model_validate({"sync": {"mappings": [dict(name="x", local_dir=str(source), remote_dir="x")]}})
    first = tmp_path / "upload.yaml"
    save_config(first, cfg)
    register_routes(first, cfg)
    cfg.app.mode = "download"
    second = tmp_path / "download.yaml"
    cfg.sync.mappings[0].local_dir = str(source / "nested")
    save_config(second, cfg)
    with pytest.raises(ValueError, match="循环"):
        register_routes(second, cfg)
    cfg.sync.mappings[0].local_dir = str(tmp_path / "downloads")
    save_config(second, cfg)
    register_routes(second, cfg)


def test_daemon_safe_reload_retains_valid_config(setup, tmp_path, monkeypatch):
    cfg, backend, state, source = setup
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    import cloudbridge.core.scheduler as scheduler
    observed = []
    class Stop:
        calls = 0
        def is_set(self):
            return False
        def set(self):
            pass
        def wait(self, duration):
            self.calls += 1
            if self.calls == 1:
                updated = load_config(path)
                updated.sync.max_concurrent_transfers = 3
                save_config(path, updated)
                return False
            if self.calls == 2:
                path.write_text("sync: [broken", encoding="utf-8")
                return False
            return True
    class Service:
        def __init__(self, config, backend, state, stop):
            self.config = config
        def run_once(self, force=False):
            observed.append(self.config.sync.max_concurrent_transfers)
    monkeypatch.setattr(scheduler.threading, "Event", Stop)
    monkeypatch.setattr(scheduler, "RcloneWebDAVBackend", lambda *args: backend)
    monkeypatch.setattr(scheduler, "Uploader", Service)
    assert run(path, "upload", daemon=True, force=False) == 0
    assert observed == [2, 3, 3]
