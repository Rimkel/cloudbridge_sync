import importlib.util
from pathlib import Path
import zipfile


def test_update_archive_preserves_private_configs_and_windows_encoding(tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/build_windows_update.py"
    spec = importlib.util.spec_from_file_location("build_update", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = path.parents[1]
    archive_path = tmp_path / "update.zip"
    assert module.build(root, archive_path) > 20
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert "start-sync.cmd" in names
        assert "cloudbridge/launcher/worker.py" in names
        assert "configs/oneclick.example.yaml" in names
        assert "usage.md" in names
        assert "docs/assets/architecture.svg" in names
        assert "docs/assets/path-mapping.svg" in names
        assert not any(name.startswith("docs/private/") for name in names)
        assert "docs/paper-sync.md" not in names
        assert not set(names) & {"configs/ubuntu.yaml", "configs/windows.yaml", "configs/oneclick.yaml"}
        assert all(not any(part in name.split("/") for part in (".venv", "OpenList", "__pycache__", ".git")) for name in names)
        assert archive.read("scripts/setup_oneclick.ps1").startswith(b"\xef\xbb\xbf")
        command = archive.read("start-sync.cmd")
        assert b"\r\n" in command and b"\n" not in command.replace(b"\r\n", b"")


def test_unlisted_documentation_is_not_a_release_member(tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/build_windows_update.py"
    spec = importlib.util.spec_from_file_location("build_update", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("docs/private/notes.md", "docs/account-notes.md", "docs/assets/private.svg"):
        item = tmp_path / name
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_text("private deployment notes", encoding="utf-8")
    selected = {p.relative_to(tmp_path).as_posix() for p in module.members(tmp_path)}
    assert not selected & {"docs/private/notes.md", "docs/account-notes.md", "docs/assets/private.svg"}
