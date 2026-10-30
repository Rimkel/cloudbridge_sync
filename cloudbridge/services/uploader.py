"""Prioritize new stable files, reuse verified hashes, and reconcile cloud state."""
from __future__ import annotations

import logging
import os
import time
import threading
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from cloudbridge.backends.base import BackendError, Cancelled, StorageBackend
from cloudbridge.config import Config, Mapping
from cloudbridge.core.models import RemoteFile, Status
from cloudbridge.core.planner import mapping_key, sha256
from cloudbridge.core.scanner import LocalFile, scan, stat_fingerprint
from cloudbridge.core.stability import is_stable
from cloudbridge.core.state import StateStore
from cloudbridge.core.transfer import execute_transfer, fail, ready

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadJob:
    key: str
    file: LocalFile
    remote_path: str
    previous: dict[str, Any]


class Uploader:
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

    def run_once(self, *, force: bool = False) -> None:
        if not self.state.scan_lock.acquire(blocking=False):
            raise ValueError("已有扫描周期运行，拒绝重复扫描")
        self._counts.clear()
        self._listing_seconds = 0.0
        started = time.monotonic()
        log.info("上传扫描开始；并发上限 %d", self.config.sync.max_concurrent_transfers)
        try:
            self._scan_once(force=force)
        finally:
            log.info("上传扫描结束: 总耗时 %.2fs；列目录 %.2fs；扫描 %d；快速跳过 %d；"
                     "哈希校验 %d；等待稳定 %d；等待重试/已达上限 %d；实际上传 %d；失败 %d",
                     time.monotonic() - started, self._listing_seconds, self._counts["scanned"],
                     self._counts["unchanged"], self._counts["hashed"], self._counts["waiting"],
                     self._counts["backoff"], self._counts["uploaded"], self._counts["failed"])
            self.state.scan_lock.release()

    def _scan_once(self, *, force: bool) -> None:
        urgent: list[UploadJob] = []
        history: list[tuple[Mapping, list[UploadJob]]] = []
        # Observe all files before slow hashing, cloud requests or copies.
        for mapping in self.config.sync.mappings:
            if not mapping.enabled or self.stop.is_set():
                continue
            root = self.config.remote_dir(mapping)
            key = mapping_key(mapping.name, mapping.local_dir, root)
            rows = self.state.mapping_records(key)
            historical: list[UploadJob] = []
            for file in scan(mapping, self.config.filters):
                if self.stop.is_set():
                    return
                self._count("scanned")
                previous = rows.get(file.relative)
                stable = is_stable(file, previous, time.time(), self.config.sync.min_file_age_seconds)
                if not stable:
                    values: dict[str, Any] = dict(observed_size=file.size, observed_mtime=file.mtime_ns,
                                                   observed_fingerprint=file.fingerprint)
                    # Legacy successes need one verified seed, never a blanket
                    # re-upload caused just by this additive schema migration.
                    legacy = bool(previous and previous["status"] == Status.SUCCESS
                                  and not previous.get("observed_fingerprint")
                                  and (previous["observed_size"], previous["observed_mtime"])
                                  == (file.size, file.mtime_ns))
                    changed = (previous is None or previous.get("observed_fingerprint") != file.fingerprint
                               or (previous["observed_size"], previous["observed_mtime"]) != (file.size, file.mtime_ns))
                    if not legacy:
                        values["status"] = Status.WAITING_STABLE
                        if changed:
                            values.update(attempts=0, next_retry=0, permanent=0)
                    if previous is None or any(previous.get(name) != value for name, value in values.items()):
                        self.state.update(key, file.relative, **values)
                    self._count("waiting")
                    continue
                assert previous is not None
                if not ready(previous, self.config.sync, time.time(), force):
                    self._count("backoff")
                    continue
                job = UploadJob(key, file, str(PurePosixPath(root) / file.relative), previous)
                if force or previous["status"] != Status.SUCCESS:
                    urgent.append(job)
                else:
                    historical.append(job)
            if historical:
                history.append((mapping, historical))

        # Actual new/changed/retry transfers precede one-time legacy hashing.
        with ThreadPoolExecutor(max_workers=self.config.sync.max_concurrent_transfers,
                                thread_name_prefix="cloudbridge-upload") as pool:
            self._run_jobs(pool, urgent, force=force)
            for mapping, jobs in history:
                if self.stop.is_set():
                    return
                started = time.monotonic()
                log.info("核对云端历史映射 %s", mapping.name)
                try:
                    try:
                        listed = self.backend.list_files(self.config.remote_dir(mapping))
                    except BackendError as exc:
                        if exc.code not in (3, 4) or not exc.retryable:
                            raise
                        # A whole mapping directory can also be removed. Each
                        # historical object is still confirmed missing below.
                        listed = []
                finally:
                    self._listing_seconds += time.monotonic() - started
                catalog: dict[str, RemoteFile] = {}
                for remote in listed:
                    if remote.path in catalog:
                        raise BackendError("云端目录列表包含重复文件；停止本轮历史核对", retryable=False)
                    catalog[remote.path] = remote
                self._run_jobs(pool, jobs, force=False, catalog=catalog)

    def _run_jobs(self, pool: ThreadPoolExecutor, jobs: list[UploadJob], *, force: bool,
                  catalog: dict[str, RemoteFile] | None = None) -> None:
        pending: set[Future[None]] = set()
        for job in jobs:
            if self.stop.is_set():
                break
            pending.add(pool.submit(self._process, job, force, catalog))
            if len(pending) >= self.config.sync.max_concurrent_transfers:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    future.result()
        for future in pending:
            future.result()

    def _check_source(self, job: UploadJob) -> bool:
        if not job.file.path.is_symlink() and stat_fingerprint(job.file.path.stat()) == job.file.fingerprint:
            return True
        self.state.update(job.key, job.file.relative, status=Status.WAITING_STABLE)
        self._count("waiting")
        return False

    def _version(self, remote: RemoteFile | None) -> str:
        return remote.version(modtime=self.config.backend.reliable_modtime,
                              hashes=self.config.backend.reliable_hashes) if remote else ""

    def _process(self, job: UploadJob, force: bool, catalog: dict[str, RemoteFile] | None) -> None:
        if self.stop.is_set():
            return
        file, previous = job.file, job.previous
        try:
            if not self._check_source(job):
                return
            remote = catalog.get(file.relative) if catalog is not None else None
            # An incomplete cached directory listing alone is not proof of deletion.
            if remote is None:
                remote = self.backend.get_metadata(job.remote_path)
            version = self._version(remote)
            # Windows ctime can mean creation time; its upload mode keeps full
            # content checks instead of trusting the Linux change-time cache.
            cached = (os.name != "nt" and not force and previous.get("hashed_fingerprint") == file.fingerprint
                      and bool(previous["sha256"]))
            if cached:
                digest = previous["sha256"]
            else:
                self._count("hashed")
                digest = sha256(file.path)
                if not self._check_source(job):
                    return
            confirmed = (not force and previous.get("last_success") is not None
                         and previous["sha256"] == digest and remote is not None and not remote.is_dir
                         and remote.size == file.size and previous["remote_version"] == version)
            if confirmed:
                if previous.get("hashed_fingerprint") != file.fingerprint or previous["status"] != Status.SUCCESS:
                    self.state.update(job.key, file.relative, status=Status.SUCCESS, last_error=None,
                                      hashed_fingerprint=file.fingerprint)
                self._count("unchanged")
                return

            # Cached content is only sufficient for a no-write decision. A real
            # copy, including repair of a missing cloud file, rehashes its source.
            if cached:
                self._count("hashed")
                digest = sha256(file.path)
                if not self._check_source(job):
                    return
            self.state.update(job.key, file.relative, status=Status.PENDING)
            def operation() -> None:
                if self.stop.is_set():
                    raise Cancelled("上传开始前已取消")
                if not self._check_source(job):
                    return
                self.backend.upload(file.path, job.remote_path)
                if (stat_fingerprint(file.path.stat()) != file.fingerprint
                        or sha256(file.path) != digest
                        or stat_fingerprint(file.path.stat()) != file.fingerprint):
                    raise BackendError("源文件在上传期间变化；等待稳定后重试")
                target = self.backend.get_metadata(job.remote_path)
                if target is None or target.is_dir or target.size != file.size:
                    raise BackendError("上传后的远端大小验证失败")
                if self.config.backend.reliable_hashes:
                    target_hash = target.hashes.get("SHA-256") or target.hashes.get("sha256")
                    if target_hash and target_hash.lower() != digest:
                        raise BackendError("上传后的远端 SHA-256 验证失败")
                self.state.update(job.key, file.relative, status=Status.SUCCESS, sha256=digest,
                                  hashed_fingerprint=file.fingerprint, remote_version=self._version(target),
                                  attempts=0, next_retry=0, permanent=0, last_error=None, last_success=time.time())
                self._count("uploaded")
                log.info("上传完成 %s", file.relative)
            execute_transfer(self.state, job.key, file.relative, operation, self.config.sync)
            if self.state.get(job.key, file.relative)["status"] == Status.FAILED:
                self._count("failed")
        except Cancelled:
            return
        except BackendError as exc:
            fail(self.state, job.key, file.relative, exc, self.config.sync, time.time())
            self._count("failed")
        except OSError as exc:
            fail(self.state, job.key, file.relative, BackendError(f"读取源文件失败: {type(exc).__name__}"),
                 self.config.sync, time.time())
            self._count("failed")
