import os
from pathlib import Path, PureWindowsPath

import pytest

from cloudbridge.cli import main
from cloudbridge.config import Config, load_config, save_config
from cloudbridge.utils.paths import local_path, native_path, overlaps, remote_path


def test_expansion_and_windows(monkeypatch, tmp_path):
    monkeypatch.setenv("CB_TEST_ROOT", str(tmp_path))
    assert local_path("$CB_TEST_ROOT/images") == tmp_path / "images"
    assert local_path("%CB_TEST_ROOT%/images") == tmp_path / "images"
    assert local_path("~/images") == Path.home() / "images"
    assert local_path("D:/中文 目录/images") == PureWindowsPath(r"D:\中文 目录\images")
    assert overlaps(local_path("D:/Images"), local_path("d:/images/sub"))
    if os.name != "nt":
        with pytest.raises(ValueError, match="Windows"):
            native_path("D:/images")


@pytest.mark.parametrize("value", ["../escape", "a/../b", "a\\b", "/absolute", "a:token", "a/./b", "a\x00b"])
def test_remote_rejects_traversal(value):
    with pytest.raises(ValueError):
        remote_path(value)


def test_conflicts_and_invalid_options(tmp_path):
    a = dict(name="a", local_dir=str(tmp_path / "a"), remote_dir="a")
    b = dict(name="b", local_dir=str(tmp_path / "a/sub"), remote_dir="b")
    with pytest.raises(ValueError, match="重叠"):
        Config.model_validate({"sync": {"mappings": [a, b]}})
    b.update(local_dir=str(tmp_path / "b"), remote_dir="a/sub")
    with pytest.raises(ValueError, match="重叠"):
        Config.model_validate({"sync": {"mappings": [a, b]}})
    with pytest.raises(ValueError):
        Config.model_validate({"sync": {"retry_atempts": 2}})


def test_directory_validation_and_roundtrip(tmp_path):
    cfg = Config.model_validate({"sync": {"mappings": [dict(name="x", local_dir=str(tmp_path / "out"), remote_dir="x")]}})
    with pytest.raises(ValueError, match="不存在"):
        cfg.validate_directories(create=True)
    cfg.app.mode = "download"
    cfg.validate_directories(create=True)
    dest = tmp_path / "config.yaml"
    save_config(dest, cfg)
    assert load_config(dest) == cfg
    assert not list(tmp_path.glob(".config.yaml.*"))


def test_cli_mapping_lifecycle(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    prefix = ["--config", str(path)]
    assert main(prefix + ["config", "init", "--mode", "upload"]) == 0
    assert main(prefix + ["config", "init", "--mode", "download"]) == 1
    assert main(prefix + ["mapping", "add", "--name", "x", "--local", str(tmp_path / "src"), "--remote", "Images"]) == 1
    (tmp_path / "src").mkdir()
    assert main(prefix + ["mapping", "add", "--name", "x", "--local", str(tmp_path / "src"), "--remote", "Images"]) == 0
    for action in ("list", "disable", "enable", "remove"):
        assert main(prefix + ["mapping", action] + ([] if action == "list" else ["x"])) == 0
    assert main(prefix + ["config", "validate"]) == 0


def test_validation_errors_do_not_echo_secrets(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text("backend:\n  password: SECRET_DO_NOT_PRINT\n", encoding="utf-8")
    assert main(["--config", str(path), "config", "validate"]) == 1
    assert "SECRET_DO_NOT_PRINT" not in capsys.readouterr().err
    path.write_text("backend: [\n  SECRET_DO_NOT_PRINT", encoding="utf-8")
    assert main(["--config", str(path), "config", "validate"]) == 1
    assert "SECRET_DO_NOT_PRINT" not in capsys.readouterr().err


def test_yaml_relative_paths_use_config_directory(tmp_path, monkeypatch):
    folder = tmp_path / "configs"
    folder.mkdir()
    path = folder / "local.yaml"
    path.write_text("sync:\n  mappings:\n    - name: x\n      local_dir: ../images\n      remote_dir: x\nstate:\n  db_path: state.db\nlogging:\n  file: logs/app.log\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    config = load_config(path)
    assert config.sync.mappings[0].local_dir == str(tmp_path / "images")
    assert config.state.db_path == str(folder / "state.db")
    assert config.logging.file == str(folder / "logs/app.log")
