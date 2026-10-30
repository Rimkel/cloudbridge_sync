"""Read-only discovery of OpenList gateways managed outside CloudBridge."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any
import urllib.error
import urllib.request

from cloudbridge.launcher.profile import Peer


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int,
                         msg: str, headers: Any, newurl: str) -> None:
        return None


def system_openlist_running(service: str) -> bool:
    """Recognize a running system unit without reading its credentials or DB."""
    if sys.platform != "linux":
        return False
    try:
        result = subprocess.run(["systemctl", "show", service, "--property=ActiveState,MainPID"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=3, check=False, shell=False)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        pid = values.get("MainPID", "")
        if result.returncode or values.get("ActiveState") != "active" or not pid.isdecimal() or int(pid) <= 0:
            return False
        # A different Unix user may hide /proc/PID/exe, but comm is normally readable.
        name = (Path("/proc") / pid / "comm").read_text().strip().lower()
        return name.startswith("openlist")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def check_external_gateway(peer: Peer) -> str:
    """Validate a loopback public API only; this is not an account-auth test."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(peer.openlist_url.rstrip("/") + "/api/public/settings", timeout=3) as response:
            raw = response.read(262145)
            if response.status != 200 or len(raw) > 262144:
                raise ValueError
            data = json.loads(raw)
        settings = data.get("data") if isinstance(data, dict) else None
        version = settings.get("version") if isinstance(settings, dict) else None
        if not isinstance(data, dict) or data.get("code") != 200 or not isinstance(version, str) or not version.strip():
            raise ValueError
        return version
    except (OSError, ValueError, urllib.error.URLError):
        raise ValueError("外部 OpenList 网关未就绪；请检查原有服务和 openlist_url，本入口不会创建新实例或修改系统服务") from None
