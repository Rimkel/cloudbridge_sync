"""Detached user processes with pinned OpenList data and cooperative stop files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.parse import urlsplit
import uuid

from cloudbridge.config import load_config
from cloudbridge.core.locking import InstanceLock
from cloudbridge.launcher.gateway import check_external_gateway, system_openlist_running
from cloudbridge.launcher.profile import Peer, encode_peer
from cloudbridge.launcher.snapshot import read_json
from cloudbridge.utils.paths import native_path, overlaps


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".launcher-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def listening(peer: Peer) -> bool:
    url = urlsplit(peer.openlist_url)
    try:
        with socket.create_connection((url.hostname, url.port or 80), timeout=1):
            return True
    except OSError:
        return False


def discover_openlist() -> tuple[str, str, str] | None:
    """Discover this user's already running Linux instance; never print its argv."""
    if sys.platform != "linux":
        return None
    for entry in Path("/proc").iterdir():
        try:
            if not entry.name.isdigit() or entry.stat().st_uid != os.getuid():
                continue
            executable = os.readlink(entry / "exe")
            if Path(executable).name.lower() not in ("openlist", "openlist.exe"):
                continue
            argv = (entry / "cmdline").read_bytes().decode().strip("\0").split("\0")
            if "server" not in argv:
                continue
            cwd = Path(os.readlink(entry / "cwd"))
            def option(name: str, default: str) -> str:
                for index, arg in enumerate(argv):
                    if arg == name and index + 1 < len(argv):
                        return argv[index + 1]
                    if arg.startswith(name + "="):
                        return arg.split("=", 1)[1]
                return default
            data = Path(option("--data", "data"))
            data = data if data.is_absolute() else cwd / data
            config = Path(option("--config", str(data / "config.json")))
            config = config if config.is_absolute() else cwd / config
            return executable, str(data.resolve()), str(config.resolve())
        except (OSError, UnicodeError, ValueError):
            continue
    return None


def prepare(peer: Peer) -> Peer:
    """Validate everything before spawning. Never initialize OpenList or authorize it."""
    peer = peer.model_copy(deep=True)
    config = load_config(peer.config_path)
    if config.app.mode != peer.mode:
        raise ValueError("启动器运行方向与同步配置不一致")
    config.validate_directories(create=False)
    for mapping in config.sync.mappings:
        if mapping.enabled and overlaps(native_path(mapping.local_dir), peer.runtime_dir):
            raise ValueError("启动器状态不能位于同步目录内")
    if peer.openlist_management == "external":
        check_external_gateway(peer)
        return peer
    if not peer.openlist_data_dir:
        cached = read_json(peer.runtime_dir / "peer.json")
        if peer.openlist_management == "auto" and cached.get("openlist_management") == "external":
            check_external_gateway(peer)
            peer.openlist_management = "external"
            return peer
        if cached.get("openlist_data_dir"):
            previous = Peer.model_validate(cached)
            peer.openlist_data_dir = previous.openlist_data_dir
            peer.openlist_config_file = previous.openlist_config_file
            peer.openlist_executable = previous.openlist_executable
        else:
            found = discover_openlist()
            if not found:
                if peer.openlist_management == "auto" and system_openlist_running(peer.openlist_service):
                    check_external_gateway(peer)
                    peer.openlist_management = "external"
                    return peer
                raise ValueError("未找到 Ubuntu 原有 OpenList 数据目录；请在一键配置 remote.openlist_data_dir 填写原目录，禁止新建空实例")
            peer.openlist_executable, peer.openlist_data_dir, peer.openlist_config_file = found
    data = peer.path(peer.openlist_data_dir)
    config_file = peer.path(peer.openlist_config_file) if peer.openlist_config_file else data / "config.json"
    for mapping in config.sync.mappings:
        if mapping.enabled and (overlaps(native_path(mapping.local_dir), data)
                                or overlaps(native_path(mapping.local_dir), config_file)):
            raise ValueError("OpenList 数据、配置及启动器状态不能位于同步目录内")
    if not (data / "data.db").is_file() or not config_file.is_file():
        raise ValueError(f"OpenList 原有 data.db/config.json 不完整，拒绝初始化空存储；请核对数据目录: {data}")
    executable = peer.openlist_executable
    candidate = peer.path(executable)
    if not candidate.is_file() and os.name == "nt" and executable == "openlist":
        candidate = peer.path("OpenList/openlist.exe")
    resolved = str(candidate) if candidate.is_file() else shutil.which(executable)
    if not resolved:
        raise ValueError("未找到 OpenList 可执行程序；请设置 openlist_executable 的完整路径")
    peer.openlist_executable = str(Path(resolved).resolve())
    peer.openlist_data_dir = str(data)
    peer.openlist_config_file = str(config_file)
    peer.openlist_management = "managed"
    return peer


def alive(info: dict[str, Any]) -> bool:
    return (info.get("state") in ("starting", "running")
            and time.time() - info.get("updated", 0) < 10)


def spawn(peer: Peer, role: str) -> subprocess.Popen[bytes] | None:
    if alive(read_json(peer.runtime_dir / f"{role}.json")):
        return None
    executable = Path(sys.executable)
    if os.name == "nt" and executable.with_name("pythonw.exe").is_file():
        executable = executable.with_name("pythonw.exe")
    command = [str(executable), "-m", "cloudbridge.launcher", "worker", role,
               "--peer", encode_peer(peer)]
    options: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS}
    peer.runtime_dir.mkdir(parents=True, exist_ok=True)
    with (peer.runtime_dir / f"{role}.log").open("a", encoding="utf-8") as log:
        return subprocess.Popen(command, cwd=peer.project_dir, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, shell=False, **options)


def start_peer(peer: Peer) -> Peer:
    peer = prepare(peer)
    with InstanceLock(peer.runtime_dir / "start.lock"):
        if not alive(read_json(peer.runtime_dir / "transfer.json")):
            # A foreground daemon or a previously installed service is not ours to stop.
            with InstanceLock(Path(str(peer.config_path) + ".lock")):
                pass
        atomic_json(peer.runtime_dir / "peer.json", peer.model_dump())
        if peer.openlist_management != "external":
            openlist = spawn(peer, "openlist")
            deadline = time.monotonic() + 30
            while not listening(peer):
                if (openlist is not None and openlist.poll() is not None) or time.monotonic() > deadline:
                    raise ValueError("OpenList 未能启动；请检查原有数据目录、端口及 OpenList 自身日志")
                time.sleep(0.25)
        transfer = spawn(peer, "transfer")
        deadline = time.monotonic() + 5
        while not alive(read_json(peer.runtime_dir / "transfer.json")):
            if (transfer is not None and transfer.poll() is not None) or time.monotonic() > deadline:
                raise ValueError("同步进程启动失败；请查看一键运行状态及同步日志")
            time.sleep(0.1)
    return peer


def stop_peer(peer: Peer, timeout: float = 15) -> bool:
    """Stop only a launcher-owned transfer; leave OpenList and user services alone."""
    with InstanceLock(peer.runtime_dir / "start.lock"):
        info = read_json(peer.runtime_dir / "transfer.json")
        if not info or info.get("state") in ("stopped", "failed"):
            return True
        nonce = info.get("nonce")
        if not nonce:
            return False
        atomic_json(peer.runtime_dir / "transfer.stop.json", {"nonce": nonce})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = read_json(peer.runtime_dir / "transfer.json")
            if current.get("nonce") != nonce or current.get("state") in ("stopped", "failed"):
                return True
            time.sleep(0.2)
        return False


def run_worker(peer: Peer, role: str) -> int:
    with InstanceLock(peer.runtime_dir / f"{role}.lock"):
        stop = threading.Event()
        finished = threading.Event()
        nonce = uuid.uuid4().hex
        info = {"pid": os.getpid(), "nonce": nonce, "state": "starting", "updated": time.time()}
        path = peer.runtime_dir / f"{role}.json"
        def heartbeat() -> None:
            while not finished.is_set():
                if read_json(peer.runtime_dir / f"{role}.stop.json").get("nonce") == nonce:
                    stop.set()
                atomic_json(path, {**info, "updated": time.time()})
                finished.wait(1)
        atomic_json(path, info)
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        code = 1
        try:
            if role == "openlist":
                code = supervise_openlist(peer, stop, info)
            else:
                from cloudbridge.core.scheduler import run
                info["state"] = "running"
                atomic_json(path, {**info, "updated": time.time()})
                code = run(peer.config_path, peer.mode, daemon=True, force=False, stop_event=stop)
            return code
        finally:
            finished.set()
            thread.join(timeout=3)
            atomic_json(path, {**info, "state": "stopped" if code == 0 else "failed",
                               "exit_code": code, "updated": time.time(),
                               "message": "" if code == 0 else "进程已退出，请查看同步日志；尚未完成的任务下次启动恢复"})


def supervise_openlist(peer: Peer, stop: threading.Event, info: dict[str, Any]) -> int:
    child: subprocess.Popen[bytes] | None = None
    try:
        while not stop.is_set():
            if child is not None and child.poll() is not None:
                return 1  # Do not endlessly restart a broken authorization/configuration.
            if listening(peer):
                info["state"] = "running"
            elif child is None:
                child = subprocess.Popen([peer.openlist_executable, "server", "--data", peer.openlist_data_dir,
                                          "--config", peer.openlist_config_file],
                                         cwd=str(Path(peer.openlist_data_dir).parent),
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, shell=False,
                                         **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
            stop.wait(1)
        return 0
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
