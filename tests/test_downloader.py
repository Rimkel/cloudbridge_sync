import pytest

from cloudbridge.core.models import Status
from cloudbridge.core.planner import mapping_key
from cloudbridge.services.downloader import Downloader
from cloudbridge.utils.paths import safe_target


def prepare(setup):
    cfg, backend, state, source = setup
    cfg.app.mode = "download"
    cfg.backend.reliable_hashes = True
    remote = backend.target("/baidu/GPU-Transfer/Images/sub/中文 file.png")
    remote.parent.mkdir(parents=True)
    remote.write_bytes(b"cloud image")
    return cfg, backend, state, source, remote


def test_new_incremental_changed_and_local_conflict(setup):
    cfg, backend, state, source, remote = prepare(setup)
    service = Downloader(cfg, backend, state)
    target = source / "sub/中文 file.png"
    service.run_once()
    assert not target.exists()
    service.run_once()
    assert target.read_bytes() == remote.read_bytes()
    service.run_once()
    assert backend.downloads == 1
    remote.write_bytes(b"new content")
    service.run_once()
    service.run_once()
    assert backend.downloads == 2 and target.read_bytes() == b"new content"
    target.write_bytes(b"local edit")
    service.run_once(force=True)
    assert target.read_bytes() == b"local edit"
    key = mapping_key("images", str(source), cfg.remote_dir(cfg.sync.mappings[0]))
    assert state.get(key, "sub/中文 file.png")["status"] == Status.CONFLICT
    remote.unlink()
    service.run_once()
    assert target.exists()


def test_interruption_never_commits_final_and_retry(setup):
    cfg, backend, state, source, remote = prepare(setup)
    service = Downloader(cfg, backend, state)
    backend.partial = True
    service.run_once()
    service.run_once()
    assert not (source / "sub/中文 file.png").exists()
    assert not list(source.rglob(".cloudbridge-*"))
    backend.partial = False
    service.run_once(force=True)
    assert (source / "sub/中文 file.png").read_bytes() == remote.read_bytes()


def test_unknown_existing_file_skip_overwrite_and_racing_creation(setup):
    cfg, backend, state, source, remote = prepare(setup)
    target = source / "sub/中文 file.png"
    target.parent.mkdir()
    target.write_bytes(b"private")
    service = Downloader(cfg, backend, state)
    service.run_once()
    service.run_once()
    assert target.read_bytes() == b"private" and backend.downloads == 0
    cfg.sync.conflict_policy = "overwrite"
    service.run_once()
    assert target.read_bytes() == remote.read_bytes()
    target.unlink()
    cfg.sync.conflict_policy = "skip"
    backend.on_download = lambda: target.write_bytes(b"appeared while downloading")
    service.run_once()
    assert target.read_bytes() == b"appeared while downloading"


@pytest.mark.parametrize("name", ["../escape.png", "/absolute.png", "CON.png", "a./x.png", "a:secret.png", "a\\x.png"])
def test_unsafe_download_path_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        safe_target(tmp_path, name)


def test_download_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    (base / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        safe_target(base, "link/x.png")
