"""SQLite-backed observations, transfers, retry times and crash recovery."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from types import TracebackType
from typing import Any

from cloudbridge.core.models import Status


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.scan_lock = threading.Lock()
        self.db = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS files (
                mapping TEXT NOT NULL, path TEXT NOT NULL,
                observed_size INTEGER, observed_mtime INTEGER,
                status TEXT NOT NULL DEFAULT 'DISCOVERED',
                sha256 TEXT, remote_version TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, next_retry REAL NOT NULL DEFAULT 0,
                permanent INTEGER NOT NULL DEFAULT 0,
                last_success REAL, last_error TEXT, last_failure REAL,
                PRIMARY KEY(mapping, path)
            );
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(files)")}
        if "last_failure" not in columns:
            self.db.execute("ALTER TABLE files ADD COLUMN last_failure REAL")
        for name in ("observed_fingerprint", "hashed_fingerprint"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE files ADD COLUMN {name} TEXT")
        self.db.commit()

    def bind(self, identity: dict[str, Any]) -> None:
        """Reject reuse across directions/endpoints; isolate mapping identity in keys."""
        encoded = json.dumps(identity, sort_keys=True)
        with self.lock, self.db:
            row = self.db.execute("SELECT value FROM settings WHERE key='identity'").fetchone()
            if row and row[0] != encoded:
                raise ValueError("状态库已绑定其他模式或远端；请使用独立 db_path，避免上传下载循环")
            self.db.execute("INSERT OR IGNORE INTO settings VALUES ('identity', ?)", (encoded,))

    def recover(self) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE files SET status=?, next_retry=0 WHERE status IN (?, ?)",
                            (Status.RETRY, Status.TRANSFERRING, Status.PENDING))

    def retry_failed(self, mappings: list[str]) -> int:
        """Atomically requeue temporary failures in the explicitly selected mappings.

        Preserve content history and failure details so a retry still protects
        local edits and diagnostics remain available until the task succeeds.
        """
        count = 0
        with self.lock, self.db:
            for mapping in dict.fromkeys(mappings):
                cursor = self.db.execute(
                    "UPDATE files SET status=?, attempts=0, next_retry=0 "
                    "WHERE mapping=? AND status=? AND permanent=0",
                    (Status.RETRY, mapping, Status.FAILED))
                count += cursor.rowcount
        return count

    def get(self, mapping: str, path: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM files WHERE mapping=? AND path=?", (mapping, path)).fetchone()
            return dict(row) if row else None

    def mapping_records(self, mapping: str) -> dict[str, dict[str, Any]]:
        """Read a scan's prior observations in one query without write amplification."""
        with self.lock:
            return {row["path"]: dict(row) for row in self.db.execute(
                "SELECT * FROM files WHERE mapping=?", (mapping,))}

    def update(self, mapping: str, path: str, **values: Any) -> None:
        allowed = {"observed_size", "observed_mtime", "status", "sha256", "remote_version",
                   "attempts", "next_retry", "permanent", "last_success", "last_error", "last_failure",
                   "observed_fingerprint", "hashed_fingerprint"}
        if not values or not set(values) <= allowed:
            raise ValueError("无效状态字段")
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO files(mapping, path) VALUES (?, ?)", (mapping, path))
            fields = ", ".join(f"{key}=?" for key in values)
            self.db.execute(f"UPDATE files SET {fields} WHERE mapping=? AND path=?",
                            (*values.values(), mapping, path))

    def claim(self, mapping: str, path: str) -> bool:
        with self.lock, self.db:
            cursor = self.db.execute("UPDATE files SET status=? WHERE mapping=? AND path=? AND status IN (?, ?)",
                                     (Status.TRANSFERRING, mapping, path, Status.PENDING, Status.RETRY))
            return cursor.rowcount == 1

    def summary(self) -> dict[str, Any]:
        with self.lock:
            counts = dict(self.db.execute("SELECT status, COUNT(*) FROM files GROUP BY status"))
            recent = self.db.execute("SELECT MAX(last_success) FROM files").fetchone()[0]
            error = self.db.execute("SELECT last_error FROM files WHERE last_error IS NOT NULL ORDER BY last_failure DESC, rowid DESC LIMIT 1").fetchone()
            return {"counts": counts, "last_success": recent, "last_error": error[0] if error else None}

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None, tb: TracebackType | None) -> None:
        self.close()
