"""Command line entry point. Run cloudbridge --help for examples."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cloudbridge.config import Config, error_message, load_config, save_config
from cloudbridge.utils.paths import native_path


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="CloudBridge 非破坏性文件中转", epilog="示例: cloudbridge --config configs/ubuntu.yaml mapping list")
    root.add_argument("--config", type=Path, default=Path.home() / ".cloudbridge/config.yaml", help="YAML 配置路径（放在子命令前）")
    sub = root.add_subparsers(dest="command", required=True)
    config = sub.add_parser("config", help="初始化或校验配置；示例: config init --mode upload")
    cs = config.add_subparsers(dest="action", required=True)
    init = cs.add_parser("init", help="创建空映射配置，不覆盖已有文件")
    init.add_argument("--mode", choices=["upload", "download"], required=True)
    cs.add_parser("validate", help="校验 YAML 及本地目录；示例: config validate")
    mapping = sub.add_parser("mapping", help="管理目录映射；示例: mapping add --name images --local /data --remote Images")
    ms = mapping.add_subparsers(dest="action", required=True)
    ms.add_parser("list", help="显示所有映射")
    add = ms.add_parser("add", help="添加映射；未给出的参数将交互式询问")
    for name in ("name", "local", "remote"):
        add.add_argument(f"--{name}", help=f"映射 {name}")
    add.add_argument("--no-recursive", action="store_true", help="仅扫描顶层文件")
    for action in ("enable", "disable", "remove"):
        item = ms.add_parser(action, help=f"{action} 指定映射；示例: mapping {action} images")
        item.add_argument("name", help="映射名称")
    for mode in ("upload", "download"):
        transfer = sub.add_parser(mode, help=f"执行 {mode}；示例: {mode} --daemon")
        group = transfer.add_mutually_exclusive_group(required=True)
        group.add_argument("--once", action="store_true", help="执行一个扫描周期；首次发现仅记录稳定性观察")
        group.add_argument("--daemon", action="store_true", help="持续扫描并在周期间隙安全重载配置")
        transfer.add_argument("--force", action="store_true", help="重新传输并重置失败限制，仍须稳定性检查；下载仍遵守冲突策略")
    sub.add_parser("status", help="显示持久状态；示例: status")
    retry = sub.add_parser("retry-failed", help="仅恢复启用映射的临时失败任务，不传输成功文件",
                           description="仅将临时 FAILED 任务重新加入队列；不启动传输，不修改成功、冲突或永久失败任务。请先停止使用此配置的同步进程。",
                           epilog="示例: cloudbridge --config configs/windows.yaml retry-failed --mapping training")
    retry.add_argument("--mapping", dest="mapping_name", help="只恢复这一组已启用映射，默认处理全部启用映射")
    doctor = sub.add_parser("doctor", help="默认只读诊断；示例: doctor，写入回读测试: doctor --write-test")
    doctor.add_argument("--write-test", action="store_true", help="显式授权上传唯一测试文件并下载回读；保留云端测试文件")
    logs = sub.add_parser("logs", help="显示最近日志；示例: logs --lines 50")
    logs.add_argument("--lines", type=int, default=50, help="输出末尾行数，默认 50")
    return root


def execute(args: argparse.Namespace) -> int:
    path = args.config.expanduser().resolve()
    if args.command == "config" and args.action == "init":
        if path.exists():
            raise ValueError(f"拒绝覆盖已有配置: {path}")
        config = Config.model_validate({"app": {"mode": args.mode}})
        save_config(path, config)
        print(f"已创建 {path}；请添加映射并配置 rclone remote。")
        return 0
    config = load_config(path)
    if args.command in ("upload", "download"):
        from cloudbridge.core.scheduler import run
        return run(path, args.command, daemon=args.daemon, force=args.force)
    if args.command == "retry-failed":
        from contextlib import ExitStack
        from cloudbridge.core.locking import InstanceLock
        from cloudbridge.core.planner import mapping_key
        from cloudbridge.core.scheduler import identity
        from cloudbridge.core.state import StateStore
        mappings = [mapping for mapping in config.sync.mappings if mapping.enabled]
        if args.mapping_name:
            mappings = [mapping for mapping in mappings if mapping.name == args.mapping_name]
            if not mappings:
                raise ValueError(f"映射不存在或未启用: {args.mapping_name}")
        with ExitStack() as stack:
            stack.enter_context(InstanceLock(Path(str(path) + ".lock")))
            db_path = native_path(config.state.db_path)
            stack.enter_context(InstanceLock(Path(str(db_path) + ".lock")))
            if not db_path.is_file():
                count = 0
            else:
                state = stack.enter_context(StateStore(db_path))
                state.bind(identity(config))
                keys = [mapping_key(mapping.name, mapping.local_dir, config.remote_dir(mapping))
                        for mapping in mappings]
                count = state.retry_failed(keys)
        print(f"已恢复 {count} 个临时失败任务；下次同步将重新尝试，已成功、冲突和永久失败任务保持原状。")
        return 0
    if args.command == "status":
        from datetime import datetime
        from cloudbridge.core.state import StateStore
        with StateStore(native_path(config.state.db_path)) as state:
            summary = state.summary()
        counts = summary["counts"]
        print(f"模式: {config.app.mode}；启用映射: {sum(m.enabled for m in config.sync.mappings)}")
        for mapping in config.sync.mappings:
            print(f"{mapping.name}: {mapping.local_dir} -> {config.remote_dir(mapping)} ({'启用' if mapping.enabled else '禁用'})")
        pending = sum(value for key, value in counts.items() if key not in ("SUCCESS", "FAILED", "CONFLICT"))
        print(f"已完成: {counts.get('SUCCESS', 0)}；待传输/稳定: {pending}；失败: {counts.get('FAILED', 0)}；冲突: {counts.get('CONFLICT', 0)}")
        recent = datetime.fromtimestamp(summary["last_success"]).astimezone().isoformat() if summary["last_success"] else "尚无"
        print(f"最近成功时间: {recent}；最近失败原因: {summary['last_error'] or '无'}")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.command == "doctor":
        from cloudbridge.diagnostics import doctor
        return doctor(config, write_test=args.write_test)
    if args.command == "logs":
        from collections import deque
        if args.lines < 1:
            raise ValueError("--lines 必须为正整数")
        with native_path(config.logging.file).open(encoding="utf-8") as stream:
            print("".join(deque(stream, maxlen=args.lines)), end="")
        return 0
    if args.command == "config":
        config.validate_directories()
        print("配置及本地目录有效")
    elif args.command == "mapping":
        data = config.model_dump()
        mappings = data["sync"]["mappings"]
        if args.action == "list":
            for m in config.sync.mappings:
                print(f"{m.name}\t{'enabled' if m.enabled else 'disabled'}\t{m.local_dir} -> {config.remote_dir(m)}")
            print(f"共 {len(mappings)} 组映射")
            return 0
        if args.action == "add":
            name = args.name or input("映射名称: ")
            local = args.local or input("本地目录: ")
            remote = args.remote or input("云端相对目录: ")
            mappings.append(dict(name=name, local_dir=local, remote_dir=remote, recursive=not args.no_recursive))
        else:
            selected = next((m for m in mappings if m["name"] == args.name), None)
            if selected is None:
                raise ValueError(f"映射不存在: {args.name}")
            if args.action == "remove":
                mappings.remove(selected)
            else:
                selected["enabled"] = args.action == "enable"
        updated = Config.model_validate(data)
        updated.validate_directories(create=True)
        save_config(path, updated)
        print("映射配置已原子保存")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if (args.command == "mapping" and args.action != "list") or (args.command == "config" and args.action == "init") or args.command == "retry-failed":
            from cloudbridge.core.locking import InstanceLock
            with InstanceLock(Path(str(args.config.expanduser().resolve()) + ".edit.lock")):
                return execute(args)
        return execute(args)
    except (ValueError, OSError, EOFError) as exc:
        print(f"错误: {error_message(exc)}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"运行错误: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
