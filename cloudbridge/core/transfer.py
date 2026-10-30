"""Persistent capped exponential backoff and transfer state transitions."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from cloudbridge.backends.base import BackendError, Cancelled
from cloudbridge.config import SyncConfig
from cloudbridge.core.models import Status
from cloudbridge.core.state import StateStore

log = logging.getLogger(__name__)


def ready(row: dict[str, Any], config: SyncConfig, now: float, force: bool = False) -> bool:
    return force or (not row["permanent"] and row["attempts"] < config.retry_attempts and now >= row["next_retry"])


def fail(state: StateStore, mapping: str, path: str, error: BackendError, config: SyncConfig,
         now: float) -> None:
    row = state.get(mapping, path)
    attempts = row["attempts"] + 1
    wait = min(config.retry_max_seconds, config.retry_base_seconds * 2 ** min(attempts - 1, 30))
    state.update(mapping, path, status=Status.FAILED, attempts=attempts,
                 next_retry=now + wait, permanent=int(not error.retryable), last_error=str(error), last_failure=now)
    log.error("传输失败 %s: %s（尝试 %d/%d）", path, error, attempts, config.retry_attempts)


def execute_transfer(state: StateStore, mapping: str, path: str, operation: Callable[[], None],
                     config: SyncConfig) -> None:
    if not state.claim(mapping, path):
        return
    try:
        operation()
    except Cancelled:
        state.update(mapping, path, status=Status.RETRY, next_retry=0)
    except BackendError as exc:
        fail(state, mapping, path, exc, config, time.time())
    except OSError as exc:
        # Do not echo arbitrary backend exception text which could contain URLs.
        fail(state, mapping, path, BackendError(f"本地文件操作失败: {type(exc).__name__}"), config, time.time())
