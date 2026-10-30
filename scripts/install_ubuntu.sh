#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CB_CONFIG="${1:-$ROOT/configs/ubuntu.yaml}"
PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "需要 Python 3.11+")'
if [[ ! -x "$ROOT/.venv/bin/python" ]] || ! "$ROOT/.venv/bin/python" -m pip --version >/dev/null 2>&1; then
    if ! "$PYTHON" -m venv "$ROOT/.venv"; then
        echo '缺少 venv/ensurepip。请按系统提示人工安装匹配的 python3-venv 后重试。' >&2
        exit 1
    fi
fi
"$ROOT/.venv/bin/python" -m pip install -e "$ROOT"
if [[ ! -e "$CB_CONFIG" ]]; then
    "$ROOT/.venv/bin/cloudbridge" --config "$CB_CONFIG" config init --mode upload
else
    echo "保留已有配置: $CB_CONFIG"
fi
"$ROOT/.venv/bin/python" - "$CB_CONFIG" <<'PY'
import sys
from pathlib import Path
from cloudbridge.config import load_config
from cloudbridge.core.state import StateStore
from cloudbridge.utils.paths import native_path
config = load_config(Path(sys.argv[1]))
native_path(config.logging.file).parent.mkdir(parents=True, exist_ok=True)
with StateStore(native_path(config.state.db_path)):
    pass
PY
if ! command -v rclone >/dev/null; then
    echo '尚未安装 rclone；请从 https://rclone.org/install/ 人工安装。'
fi
echo '完成本地安装。下一步：rclone config，添加目录映射，执行 doctor，再手动授权写入测试。'
echo '常驻服务需显式运行 scripts/install_systemd.sh；本脚本不会启动或启用后台服务。'
