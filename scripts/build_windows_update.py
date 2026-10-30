"""Build a root-relative update archive from an explicit source allowlist.

Usage: .venv/bin/python scripts/build_windows_update.py
Never includes live configs, credentials, virtualenvs or user transfer data.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import zipfile


PUBLIC_DOCS = (
    "acceptance.md", "oneclick.md", "openlist.md", "ubuntu.md", "windows.md",
    "troubleshooting.md", "development.md", "release.md",
)


def members(root: Path) -> list[Path]:
    paths = [root / name for name in (
        "pyproject.toml", "README.md", "usage.md", "setup-oneclick.cmd", "start-sync.cmd",
        "view-progress.cmd", "stop-sync.cmd")]
    for directory, suffixes in (("cloudbridge", {".py"}), ("scripts", {".py", ".ps1", ".sh"}),
                                ("tests", {".py"})):
        paths.extend(path for path in (root / directory).rglob("*")
                     if path.is_file() and not path.is_symlink() and path.suffix in suffixes)
    paths.extend((root / "configs").glob("*.example.yaml"))
    # Never recursively include docs: local deployment notes may contain private
    # hosts and paths even when they do not contain credentials.
    paths.extend(root / "docs" / name for name in PUBLIC_DOCS)
    paths.extend(root / "docs/assets" / name for name in ("architecture.svg", "path-mapping.svg"))
    if (root / "LICENSE").is_file():
        paths.append(root / "LICENSE")
    return sorted(set(paths))


def build(root: Path, output: Path) -> int:
    root = root.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = members(root)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("拒绝打包符号链接或项目外文件")
            content = path.read_bytes()
            if path.suffix in (".ps1", ".cmd"):
                text = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\n", "\r\n")
                content = text.encode("utf-8-sig" if path.suffix == ".ps1" else "utf-8")
            archive.writestr(path.relative_to(root).as_posix(), content)
    return len(files)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "dist/cloudbridge-oneclick-update.zip")
    args = parser.parse_args()
    count = build(root, args.output)
    print(f"{args.output}: {count} source files")


if __name__ == "__main__":
    main()
