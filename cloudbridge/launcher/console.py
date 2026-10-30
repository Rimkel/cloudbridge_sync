"""Windows console controller and persistent SSH status stream."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any

from cloudbridge.launcher.profile import Peer, Profile, ssh_command
from cloudbridge.launcher.snapshot import snapshot
from cloudbridge.launcher.worker import atomic_json, prepare, start_peer, stop_peer


def clean(value: object) -> str:
    return "".join(c for c in str(value) if c.isprintable())


def format_side(title: str, data: dict[str, Any]) -> str:
    if "error" in data:
        return f"{title}: {clean(data['error'])}"
    counts = data.get("counts", {})
    waiting = sum(value for key, value in counts.items() if key not in ("SUCCESS", "FAILED", "CONFLICT", "TRANSFERRING"))
    worker = data.get("transfer", {})
    states = {"running": "进程运行中", "starting": "启动中", "stopped": "已停止", "failed": "已退出/失败", "unknown": "心跳过期"}
    lines = [f"{title}: {states.get(worker.get('state'), '非一键进程/未启动')} | "
             f"已记录成功 {counts.get('SUCCESS', 0)} | 等待 {waiting} | "
             f"传输中 {counts.get('TRANSFERRING', 0)} | 失败 {counts.get('FAILED', 0)} | 冲突 {counts.get('CONFLICT', 0)}"]
    if data.get("openlist", {}).get("state") == "external":
        lines.append("  OpenList: 复用原有服务，不读取或重建其数据目录")
    for name in data.get("active", []):
        lines.append("  当前文件: " + clean(name))
    if data.get("last_success"):
        lines.append("  最近成功: " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(data["last_success"])))
    if data.get("last_error"):
        lines.append("  文件错误记录: " + clean(data["last_error"]))
    if worker.get("message"):
        lines.append("  " + clean(worker["message"]))
    return "\n".join(lines)


def safe_snapshot(peer: Peer) -> dict[str, Any]:
    try:
        return snapshot(peer)
    except Exception:
        return {"error": "读取状态暂时失败；请检查同步配置与状态数据库"}


def agent(peer: Peer, action: str, interval: float) -> int:
    if action == "check":
        peer = prepare(peer)
        atomic_json(peer.runtime_dir / "peer.json", peer.model_dump())
        print(json.dumps({"checked": True}), flush=True)
        return 0
    if action == "stop":
        stopped = stop_peer(peer)
        print(json.dumps({"stopped": stopped}), flush=True)
        return 0 if stopped else 1
    if action == "start":
        peer = start_peer(peer)
    try:
        while True:
            print(json.dumps(safe_snapshot(peer), ensure_ascii=False), flush=True)
            time.sleep(interval)
    except (BrokenPipeError, KeyboardInterrupt):
        return 0


def control(profile: Profile, action: str) -> int:
    if os.name != "nt":
        raise ValueError("start/watch/stop/check 是 Windows 控制入口；Ubuntu 仅运行 agent/worker")
    if action == "check":
        peer = prepare(profile.local)
        atomic_json(peer.runtime_dir / "peer.json", peer.model_dump())
        subprocess.run(ssh_command(profile, "check"), check=True)
        print("两端配置及 OpenList 网关检查通过；尚未启动传输。")
        return 0
    if action == "stop":
        local_ok = stop_peer(profile.local)
        result = subprocess.run(ssh_command(profile, "stop"), check=False)
        print("Windows 同步已停止。" if local_ok else "Windows 同步仍在退出，请稍后查看状态。")
        print("OpenList 保持运行；其他手动启动或服务管理的进程不受此入口控制。")
        return 0 if local_ok and result.returncode == 0 else 1
    if action == "start":
        # Validate local state before starting any remote transfers.
        prepare(profile.local)
    remote = subprocess.Popen(ssh_command(profile, "start" if action == "start" else "watch"),
                              stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    updates: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=10)
    def reader() -> None:
        assert remote.stdout is not None
        try:
            for line in remote.stdout:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue  # Never echo arbitrary remote banners/responses into the dashboard.
                if isinstance(value, dict):
                    try:
                        updates.put_nowait(value)
                    except queue.Full:
                        pass
        finally:
            try:
                updates.put_nowait(None)
            except queue.Full:
                pass
    threading.Thread(target=reader, daemon=True).start()
    print("正在连接 Ubuntu；如 SSH 要求，请在此输入你的 SSH 密码。", flush=True)
    last_remote: dict[str, Any] = {"error": "连接中"}
    started = False
    deadline = time.monotonic() + 90
    try:
        while True:
            try:
                update = updates.get(timeout=profile.refresh_seconds)
            except queue.Empty:
                update = {}
                if not started and time.monotonic() > deadline:
                    raise ValueError("SSH 启动或状态返回超时；远端可能已经启动，请用查看进度入口核对")
                if remote.poll() is not None:
                    update = None
            if update is None:
                print("SSH 进度连接已断开。已启动的后台同步继续运行；重新打开 view-progress.cmd 查看。")
                return 1
            if update:
                last_remote = update
                if not started:
                    if action == "start":
                        try:
                            start_peer(profile.local)
                        except (ValueError, OSError):
                            print("Windows 端启动未完成；Ubuntu 端可能已在后台上传。请修复后重试，或运行 stop-sync.cmd 停止。")
                            raise
                    started = True
            if not started:
                continue
            local = safe_snapshot(profile.local)
            display = ("CloudBridge " + time.strftime("%H:%M:%S") + "\n\n"
                       + format_side("Ubuntu 上传", last_remote) + "\n\n"
                       + format_side("Windows 下载", local)
                       + "\n\n关闭窗口或 Ctrl+C 仅关闭预览。停止同步请运行 stop-sync.cmd。"
                       + "\n计数来自本机状态库历史记录，不代表目录总进度；不显示字节速度/百分比。\n")
            if sys.stdout.isatty():
                # Enable virtual terminal processing on supported Windows consoles.
                import ctypes
                from ctypes import wintypes
                kernel = ctypes.windll.kernel32
                kernel.GetStdHandle.argtypes = [wintypes.DWORD]
                kernel.GetStdHandle.restype = wintypes.HANDLE
                kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                handle = kernel.GetStdHandle(-11)
                mode = wintypes.DWORD()
                prefix = ""
                if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                    if kernel.SetConsoleMode(handle, mode.value | 4):
                        prefix = "\033[2J\033[H"
                print(prefix + display, end="", flush=True)
            else:
                print(display, flush=True)
    except KeyboardInterrupt:
        print("\n预览已关闭；后台同步继续。")
        return 0
    finally:
        if remote.poll() is None:
            remote.terminate()  # Only the SSH preview process, never the detached transfer.
        try:
            remote.wait(timeout=5)
        except subprocess.TimeoutExpired:
            remote.kill()
            remote.wait()
