from cloudbridge.config import FiltersConfig, Mapping
from cloudbridge.core.scanner import scan


def test_filters_recursion_temporary_size_and_symlinks(tmp_path):
    (tmp_path / "sub").mkdir()
    for name in ("ok.PNG", "skip.tmp", "no.txt", "huge.png"):
        (tmp_path / name).write_bytes(b"x" * (2000 if name == "huge.png" else 1))
    (tmp_path / "sub/中文 图.png").write_bytes(b"x")
    (tmp_path / "sub.tmp").mkdir()
    (tmp_path / "sub.tmp/not.png").write_bytes(b"x")
    (tmp_path / "link.png").symlink_to(tmp_path / "ok.PNG")
    mapping = Mapping(name="a", local_dir=str(tmp_path), remote_dir="a")
    filters = FiltersConfig(max_file_size_mb=.001)
    assert {f.relative for f in scan(mapping, filters)} == {"ok.PNG", "sub/中文 图.png"}
    mapping.recursive = False
    assert [f.relative for f in scan(mapping, filters)] == ["ok.PNG"]
