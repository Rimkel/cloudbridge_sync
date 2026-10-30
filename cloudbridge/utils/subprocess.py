"""Cancellable rclone runner; raw stderr never enters logs or exceptions."""
from __future__ import annotations

import subprocess
import os
import re
import threading
import time

from cloudbridge.backends.base import BackendError, Cancelled


def describe_failure(stderr: str, code: int) -> BackendError:
    """Return fixed safe labels; never include remote responses or URLs."""
    lower = stderr.lower()
    if "expired_token" in lower or "refresh token has been used" in lower:
        return BackendError("百度刷新令牌已失效（expired_token）；请在当前 OpenList 实例重新授权", retryable=False, code=code)
    if "invalid_client" in lower:
        return BackendError("百度应用凭据无效（invalid_client）；请检查当前 OpenList 的 client_id 与 client_secret", retryable=False, code=code)
    if "invalid_grant" in lower:
        return BackendError("百度授权无效或已撤销（invalid_grant）；请检查当前 OpenList 授权", retryable=False, code=code)
    for status, phrase, explanation in (
        (401, r"unauthori[sz]ed", "WebDAV 登录认证失败；请检查当前机器 rclone 使用的 OpenList 用户名和密码"),
        (403, r"forbidden", "WebDAV 请求被拒绝；请检查当前 OpenList 用户权限及百度存储状态"),
    ):
        # A filename or request ID containing '401'/'403' is not an HTTP error.
        explicit = rf"\b(?:http(?:/\d(?:\.\d)?)?\s+|status(?:\s+code)?\s*[:=]?\s*){status}\b"
        reason = rf"\b{status}\s+{phrase}\b"
        if re.search(explicit, lower) or re.search(reason, lower):
            return BackendError(f"HTTP {status}：{explanation}", retryable=False, code=code)
    if re.search(r"\b(?:unauthori[sz]ed|forbidden|invalid token|authentication (?:failed|failure|error))\b", lower):
        return BackendError("后端认证失效或权限不足；请检查私有 rclone 配置及 OpenList 授权", retryable=False, code=code)
    permanent = code in (2, 6, 7)
    return BackendError(f"rclone 执行失败（退出码 {code}，{'永久' if permanent else '可重试'}错误）；原始错误未显示以保护凭据",
                        retryable=not permanent, code=code)


class CommandRunner:
    def __init__(self, timeout: float, stop: threading.Event | None = None) -> None:
        self.timeout = timeout
        self.stop = stop or threading.Event()

    def run(self, args: list[str]) -> str:
        if self.stop.is_set():
            raise Cancelled("任务已取消")
        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding="utf-8", errors="replace", shell=False,
                                       **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        except FileNotFoundError:
            raise BackendError("未找到 rclone；请安装 rclone 或配置 backend.executable", retryable=False) from None
        except OSError:
            raise BackendError("无法启动 rclone；请检查程序路径及执行权限", retryable=False) from None
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                if self.stop.is_set():
                    raise Cancelled("任务已取消")
                if time.monotonic() >= deadline:
                    raise BackendError("rclone 执行超时")
                try:
                    stdout, stderr = process.communicate(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
        if process.returncode:
            raise describe_failure(stderr, process.returncode)
        return stdout
