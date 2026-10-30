"""Run `python -m cloudbridge.launcher --help` for one-click commands."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

from cloudbridge.config import error_message
from cloudbridge.launcher.profile import decode_peer, load_profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CloudBridge Windows 一键同步与进度预览",
                                     epilog="示例: python -m cloudbridge.launcher --profile configs/oneclick.yaml start")
    parser.add_argument("--profile", type=Path, default=Path("configs/oneclick.yaml"), help="不含密码的启动配置")
    sub = parser.add_subparsers(dest="command", required=True)
    for action, help_text in (("start", "启动两端并预览；关闭窗口后继续同步"), ("watch", "仅查看进度"),
                              ("stop", "安全停止两端一键同步，保留 OpenList"), ("check", "只检查两端配置，不启动同步")):
        sub.add_parser(action, help=help_text)
    agent = sub.add_parser("agent", help="Ubuntu 内部控制入口，由 SSH 调用")
    agent.add_argument("action", choices=["start", "watch", "stop", "check"])
    agent.add_argument("--peer", required=True, help="编码后的非敏感运行配置")
    agent.add_argument("--interval", type=float, default=3, help="状态采样秒数，1 至 60")
    worker = sub.add_parser("worker", help="内部后台进程，请使用 start 启动")
    worker.add_argument("role", choices=["openlist", "transfer"])
    worker.add_argument("--peer", required=True, help="编码后的非敏感运行配置")
    args = parser.parse_args(argv)
    try:
        if args.command == "agent":
            if not 1 <= args.interval <= 60:
                raise ValueError("状态采样间隔须为 1 至 60 秒")
            from cloudbridge.launcher.console import agent as run_agent
            return run_agent(decode_peer(args.peer), args.action, args.interval)
        if args.command == "worker":
            from cloudbridge.launcher.worker import run_worker
            peer = decode_peer(args.peer)
            # pythonw.exe has no console streams. Keep safe CloudBridge errors available.
            if sys.stderr is None or sys.stdout is None:
                peer.runtime_dir.mkdir(parents=True, exist_ok=True)
                stream = (peer.runtime_dir / f"{args.role}.log").open("a", encoding="utf-8", buffering=1)
                sys.stdout = sys.stderr = stream
            return run_worker(peer, args.role)
        from cloudbridge.launcher.console import control
        return control(load_profile(args.profile), args.command)
    except (ValueError, OSError, RuntimeError) as exc:
        print("一键同步错误: " + error_message(exc), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError:
        print("SSH 远端检查失败；请检查上方错误及已有 SSH 登录配置。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
