#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CB_CONFIG="${1:-$ROOT/configs/ubuntu.yaml}"
"$ROOT/.venv/bin/python" - "$ROOT" "$CB_CONFIG" <<'PY'
import os
import sys
from pathlib import Path
from cloudbridge.config import load_config
root, config = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).expanduser().resolve()
cfg = load_config(config)
if cfg.app.mode != 'upload':
    raise SystemExit('服务要求 upload 配置')
cfg.validate_directories()
def quote(path, *, executable=False):
    value = str(path)
    if any(ord(c) < 32 for c in value):
        raise SystemExit('服务路径不能包含控制字符')
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    if executable:
        value = value.replace('$', '$$')
    return '"' + value + '"'
service = Path.home() / '.config/systemd/user/cloudbridge-upload.service'
service.parent.mkdir(parents=True, exist_ok=True)
content = f'''[Unit]
Description=CloudBridge incremental uploader

[Service]
Type=simple
WorkingDirectory={quote(root)}
ExecStart={quote(root / '.venv/bin/python', executable=True)} -m cloudbridge --config {quote(config, executable=True)} upload --daemon
Restart=on-failure
RestartSec=30
TimeoutStopSec=15
UMask=0077

[Install]
WantedBy=default.target
'''
with service.open('x', encoding='utf-8') as stream:
    stream.write(content)
print(f'已写入 {service}；未启动或启用开机运行。')
PY
systemctl --user daemon-reload
echo '显式启动: systemctl --user start cloudbridge-upload'
echo '显式开机启动: systemctl --user enable cloudbridge-upload'
echo '查询/停止: systemctl --user status cloudbridge-upload / systemctl --user stop cloudbridge-upload'
