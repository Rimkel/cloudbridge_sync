"""Conservative downloads through staging files and guarded final commits."""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path, PurePosixPath

from cloudbridge.backends.base import BackendError, Cancelled, StorageBackend
from cloudbridge.config import Config
from cloudbridge.core.models import RemoteFile, Status
from cloudbridge.core.planner import mapping_key, sha256
from cloudbridge.core.scanner import matches
from cloudbridge.core.state import StateStore
from cloudbridge.core.transfer import execute_transfer, fail, ready
from cloudbridge.utils.paths import native_path, safe_target

log = logging.getLogger(__name__)


class Downloader:
    def __init__(self, config: Config, backend: StorageBackend, state: StateStore,
                 stop: threading.Event | None = None) -> None:
        self.config, self.backend, self.state = config, backend, state
        self.stop = stop or threading.Event()
        self._counts: Counter[str] = Counter()
        self._counts_lock = threading.Lock()
        self._listing_seconds = 0.0

    def _count(self, name: str, amount: int = 1) -> None:
        with self._counts_lock:
            self._counts[name] += amount

    def _version(self, file: RemoteFile) -> str:
        return file.version(modtime=self.config.backend.reliable_modtime,
                            hashes=self.config.backend.reliable_hashes)

    def run_once(self, *, force: bool = False) -> None:
        if not self.state.scan_lock.acquire(blocking=False):
            raise ValueError("已有扫描周期运行，拒绝重复扫描")
        started = time.monotonic()
        self._counts.clear()
        self._listing_seconds = 0.0
        log.info("下载扫描开始；并发上限 %d", self.config.sync.max_concurrent_transfers)
        try:
            self._scan_once(force=force)
        finally:
            log.info("下载扫描结束: 总耗时 %.2fs；列目录 %.2fs；列出 %d；快速跳过 %d；"
                     "等待稳定 %d；等待重试/已达上限 %d；实际下载 %d；失败 %d；冲突 %d",
                     time.monotonic() - started, self._listing_seconds, self._counts["listed"],
                     self._counts["unchanged"], self._counts["waiting"], self._counts["backoff"],
                     self._counts["downloaded"], self._counts["failed"], self._counts["conflict"])
            self.state.scan_lock.release()

    def _scan_once(self, *, force: bool = False) -> None:
        limit = self.config.sync.max_concurrent_transfers
        with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="cloudbridge-download") as pool:
            pending: set[Future[None]] = set()
            for mapping in self.config.sync.mappings:
                if self.stop.is_set():
                    break
                if not mapping.enabled:
                    continue
                base = native_path(mapping.local_dir)
                root = self.config.remote_dir(mapping)
                key = mapping_key(mapping.name, mapping.local_dir, root)
                listing_started = time.monotonic()
                log.info("读取云端映射目录 %s", mapping.name)
                try:
                    files = self.backend.list_files(root)
                finally:
                    self._listing_seconds += time.monotonic() - listing_started
                self._count("listed", len(files))
                # Validate the entire mapping before any worker can commit a file.
                validated = self._validate_files(base, files, mapping.recursive)
                for remote, target in validated:
                    if self.stop.is_set():
                        break
                    if not matches(remote.path, remote.size, self.config.filters):
                        continue
                    pending.add(pool.submit(self._process, base, root, key, remote, target, force))
                    # Bound submitted work as well as active threads, even for huge listings.
                    if len(pending) >= limit:
                        done, pending = wait(pending, return_when=FIRST_COMPLETED)
                        for future in done:
                            future.result()
            for future in pending:
                future.result()

    @staticmethod
    def _validate_files(base: Path, files: list[RemoteFile], recursive: bool) -> list[tuple[RemoteFile, Path]]:
        targets: set[str] = set()
        directories: set[str] = set()
        prefixes: dict[str, str] = {}
        validated: list[tuple[RemoteFile, Path]] = []
        for remote in files:
            if remote.is_dir or (not recursive and "/" in remote.path):
                continue
            target = safe_target(base, remote.path)
            parts = target.relative_to(base).parts
            for length in range(1, len(parts) + 1):
                prefix = "/".join(parts[:length])
                old = prefixes.setdefault(prefix.casefold(), prefix)
                if old != prefix:
                    raise BackendError("云端目录或文件存在大小写冲突；停止该周期以保护本地路径", retryable=False)
            folded = str(target).casefold()
            parents = {str(parent).casefold() for parent in target.parents
                       if parent != base and parent.is_relative_to(base)}
            if folded in targets:
                raise BackendError("云端存在大小写冲突的文件名；停止该周期以保护本地文件", retryable=False)
            if folded in directories or parents & targets:
                raise BackendError("云端文件与目录路径冲突；停止该周期以保护本地文件", retryable=False)
            targets.add(folded)
            directories.update(parents)
            validated.append((remote, target))
        return validated

    def _process(self, base: Path, root: str, key: str, remote: RemoteFile,
                 target: Path, force: bool) -> None:
        # Arguments bind each job's paths; no closures capture a changing scan loop.
        if self.stop.is_set():
            return
        version = self._version(remote)
        observation = int.from_bytes(hashlib.sha256(version.encode()).digest()[:7], "big")
        previous = self.state.get(key, remote.path)
        stable = bool(previous and previous["observed_size"] == remote.size and previous["observed_mtime"] == observation)
        if not stable:
            self.state.update(key, remote.path, observed_size=remote.size, observed_mtime=observation,
                              status=Status.WAITING_STABLE, attempts=0, next_retry=0, permanent=0)
            self._count("waiting")
            return
        row = previous
        if not ready(row, self.config.sync, time.time(), force):
            self._count("backoff")
            return
        def recheck_target() -> None:
            try:
                safe_target(base, remote.path)
            except ValueError:
                raise BackendError("下载目标路径在扫描后发生不安全变化；已跳过", retryable=False) from None
        try:
            recheck_target()
            # No write is planned for this version: don't reread all historical
            # contents or commit duplicate SQLite updates every polling cycle.
            # SUCCESS means the last transfer succeeded, not a fresh hash audit.
            # Any actual replacement below still verifies the local digest twice.
            if (not force and row["status"] == Status.SUCCESS and row["sha256"]
                    and row["remote_version"] == version and target.is_file()
                    and target.stat().st_size == remote.size):
                self._count("unchanged")
                return
            before = sha256(target) if target.is_file() else None
            known_local = before is not None and row["sha256"] == before
            if target.exists() and (not target.is_file() or not known_local):
                if self.config.sync.conflict_policy == "skip":
                    self._conflict(key, remote.path)
                    return
            if not force and target.is_file() and known_local and row["remote_version"] == version:
                self.state.update(key, remote.path, status=Status.SUCCESS, last_error=None)
                self._count("unchanged")
                return
            full_remote = str(PurePosixPath(root) / remote.path)
            self.state.update(key, remote.path, status=Status.PENDING)
            def operation() -> None:
                if self.stop.is_set():
                    raise Cancelled("下载开始前已取消")
                # Revalidate after mkdir to reject ordinary symlink escapes.
                target.parent.mkdir(parents=True, exist_ok=True)
                recheck_target()
                fd, temporary = tempfile.mkstemp(prefix=".cloudbridge-", suffix=".part", dir=target.parent)
                os.close(fd)
                stage = Path(temporary)
                try:
                    self.backend.download(full_remote, stage)
                    if self.stop.is_set():
                        raise Cancelled("下载提交前已取消")
                    if stage.stat().st_size != remote.size:
                        raise BackendError("下载文件大小不匹配")
                    after_remote = self.backend.get_metadata(full_remote)
                    if after_remote is None or self._version(after_remote) != version:
                        raise BackendError("云端文件在下载期间发生变化")
                    digest = sha256(stage)
                    if self.config.backend.reliable_hashes:
                        expected = remote.hashes.get("SHA-256") or remote.hashes.get("sha256")
                        if expected and digest != expected.lower():
                            raise BackendError("下载 SHA-256 不匹配")
                    recheck_target()
                    current = sha256(target) if target.is_file() else None
                    if self.config.sync.conflict_policy == "skip" and current != before:
                        self._conflict(key, remote.path)
                        return
                    with stage.open("r+b") as stream:
                        os.fsync(stream.fileno())
                    if self.config.sync.conflict_policy == "skip" and before is None:
                        # Windows rename refuses existing targets. POSIX
                        # link provides the equivalent no-clobber commit.
                        try:
                            if os.name == "nt":
                                os.rename(stage, target)
                            else:
                                os.link(stage, target)
                        except FileExistsError:
                            self._conflict(key, remote.path)
                            return
                    else:
                        os.replace(stage, target)
                    self.state.update(key, remote.path, status=Status.SUCCESS, sha256=digest,
                                      remote_version=version, attempts=0, next_retry=0, permanent=0,
                                      last_error=None, last_success=time.time())
                    log.info("下载完成 %s", remote.path)
                    self._count("downloaded")
                finally:
                    stage.unlink(missing_ok=True)
            execute_transfer(self.state, key, remote.path, operation, self.config.sync)
            if self.state.get(key, remote.path)["status"] == Status.FAILED:
                self._count("failed")
        except Cancelled:
            return
        except BackendError as exc:
            fail(self.state, key, remote.path, exc, self.config.sync, time.time())
            self._count("failed")
        except OSError as exc:
            fail(self.state, key, remote.path, BackendError(f"读取下载目标失败: {type(exc).__name__}"),
                 self.config.sync, time.time())
            self._count("failed")

    def _conflict(self, key: str, path: str) -> None:
        self._count("conflict")
        self.state.update(key, path, status=Status.CONFLICT, last_error="本地文件存在未知版本或下载期间被修改；已跳过", last_failure=time.time())
        log.warning("本地文件冲突，跳过 %s", path)
