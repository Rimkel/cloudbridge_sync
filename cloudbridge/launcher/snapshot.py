"""Read-only SQLite progress: file states, never invented byte percentages."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from cloudbridge.config import load_config
from cloudbridge.launcher.profile import Peer
from cloudbridge.utils.paths import native_path


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def snapshot(peer: Peer) -> dict[str, Any]:
    config = load_config(peer.config_path)
    result: dict[str, Any] = {"mode": config.app.mode, "counts": {}, "active": [],
                              "last_success": None, "last_error": None}
    path = native_path(config.state.db_path)
    if path.exists():
        # Never initialize, migrate or recover a DB from the preview window.
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            result["counts"] = dict(db.execute("SELECT status, COUNT(*) FROM files GROUP BY status"))
            result["active"] = [row[0] for row in db.execute(
                "SELECT path FROM files WHERE status='TRANSFERRING' LIMIT 5")]
            result["last_success"] = db.execute("SELECT MAX(last_success) FROM files").fetchone()[0]
            row = db.execute("SELECT last_error FROM files WHERE last_error IS NOT NULL ORDER BY rowid DESC LIMIT 1").fetchone()
            result["last_error"] = row[0] if row else None
    for role in ("openlist", "transfer"):
        if role == "openlist" and read_json(peer.runtime_dir / "peer.json").get("openlist_management") == "external":
            result[role] = {"state": "external", "message": "复用外部 OpenList；由原服务管理，不受一键入口启停控制"}
            continue
        info = read_json(peer.runtime_dir / f"{role}.json")
        if info.get("state") in ("starting", "running") and time.time() - info.get("updated", 0) > 10:
            info = {**info, "state": "unknown", "message": "心跳过期；进程可能已退出"}
        result[role] = info
    return result
