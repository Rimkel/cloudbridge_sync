import sys
from pathlib import Path

import pytest

from cloudbridge.config import Config, save_config


def test_systemd_generation_quotes_paths_and_never_overwrites(tmp_path, monkeypatch):
    project = tmp_path / '项目 space $name %test "quoted"'
    project.mkdir()
    source = project / "images"
    source.mkdir()
    cfg = Config.model_validate({"sync": {"mappings": [dict(name="x", local_dir=str(source), remote_dir="x")]}})
    path = project / "config.yaml"
    save_config(path, cfg)
    script = Path(__file__).resolve().parents[1] / "scripts/install_systemd.sh"
    program = script.read_text(encoding="utf-8").split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user"))
    monkeypatch.setattr(sys, "argv", [str(script), str(project), str(path)])
    exec(compile(program, str(script), "exec"), {})
    service = tmp_path / "user/.config/systemd/user/cloudbridge-upload.service"
    content = service.read_text(encoding="utf-8")
    assert "$$name" in content.split("ExecStart=", 1)[1]
    assert "%%test" in content
    assert '\\"quoted\\"' in content
    assert "WorkingDirectory=" in content and "upload --daemon" in content
    with pytest.raises(FileExistsError):
        exec(compile(program, str(script), "exec"), {})
    assert service.read_text(encoding="utf-8") == content
