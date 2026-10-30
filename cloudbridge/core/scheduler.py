"""Serial scan cycles, graceful shutdown and validated reload between cycles."""
from __future__ import annotations

import hashlib
import logging
import signal
import threading
from contextlib import ExitStack
from pathlib import Path

from cloudbridge.backends.base import BackendError, Cancelled
from cloudbridge.backends.rclone_webdav import RcloneWebDAVBackend
from cloudbridge.config import Config, error_message, load_config
from cloudbridge.core.locking import InstanceLock
from cloudbridge.core.routes import register_routes
from cloudbridge.core.state import StateStore
from cloudbridge.logging_config import configure_logging
from cloudbridge.services.uploader import Uploader
from cloudbridge.utils.paths import native_path
from cloudbridge.utils.subprocess import CommandRunner

log = logging.getLogger(__name__)


def identity(config: Config) -> dict[str, str]:
    return dict(mode=config.app.mode, remote=config.backend.remote(config.app.mode),
                cloud_root=config.backend.cloud_root)


def run(config_path: Path, mode: str, *, daemon: bool, force: bool,
        stop_event: threading.Event | None = None) -> int:
    config = load_config(config_path)
    if config.app.mode != mode:
        raise ValueError(f"配置模式是 {config.app.mode}，不能执行 {mode}")
    config.validate_directories(create=True)
    register_routes(config_path, config)
    configure_logging(config)
    stop = stop_event if stop_event is not None else threading.Event()
    handlers = {}
    def shutdown(signum: int, frame: object) -> None:
        stop.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        handlers[sig] = signal.signal(sig, shutdown)
    try:
        with ExitStack() as stack:
            stack.enter_context(InstanceLock(Path(str(config_path) + ".lock")))
            db_path = native_path(config.state.db_path)
            stack.enter_context(InstanceLock(Path(str(db_path) + ".lock")))
            state = stack.enter_context(StateStore(db_path))
            state.bind(identity(config))
            state.recover()
            locked: set[str] = set()
            while not stop.is_set():
                for mapping in config.sync.mappings:
                    if not mapping.enabled or mapping.local_dir in locked:
                        continue
                    # Shared per-user lock prevents two configs using the same
                    # local directory concurrently, including opposite modes.
                    key = hashlib.sha256(str(native_path(mapping.local_dir)).casefold().encode()).hexdigest()
                    stack.enter_context(InstanceLock(Path.home() / ".cloudbridge/locks" / key))
                    locked.add(mapping.local_dir)
                backend = RcloneWebDAVBackend(config.backend, mode, CommandRunner(config.backend.timeout_seconds, stop))
                try:
                    backend.check_connection()
                    if mode == "upload":
                        service = Uploader(config, backend, state, stop)
                    else:
                        from cloudbridge.services.downloader import Downloader
                        service = Downloader(config, backend, state, stop)
                    service.run_once(force=force)
                except Cancelled:
                    break
                except BackendError as exc:
                    log.error("扫描周期失败: %s", exc)
                    if not daemon or not exc.retryable:
                        return 1
                except OSError as exc:
                    log.error("扫描本地目录失败: %s", type(exc).__name__)
                    if not daemon:
                        return 1
                if not daemon:
                    summary = state.summary()["counts"]
                    print("扫描完成；首次发现的文件需下一次扫描确认稳定。")
                    return 1 if summary.get("FAILED", 0) or summary.get("CONFLICT", 0) else 0
                if stop.wait(config.sync.interval_seconds):
                    break
                try:
                    candidate = load_config(config_path)
                    if identity(candidate) != identity(config) or native_path(candidate.state.db_path) != db_path:
                        raise ValueError("模式、remote、cloud_root 或 db_path 变更需要重启并使用独立状态库")
                    candidate.validate_directories(create=True)
                    register_routes(config_path, candidate)
                    if candidate != config:
                        config = candidate
                        configure_logging(config)
                        log.info("已在扫描间隙安全重新加载配置")
                except (ValueError, OSError) as exc:
                    log.error("配置重载失败，保留当前配置: %s", error_message(exc))
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    return 0
