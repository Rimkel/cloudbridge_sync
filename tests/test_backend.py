import json
import sys
import threading

import pytest

from cloudbridge.backends.base import BackendError, Cancelled
from cloudbridge.backends.rclone_webdav import RcloneWebDAVBackend
from cloudbridge.config import BackendConfig
from cloudbridge.utils.subprocess import CommandRunner, describe_failure


class Runner:
    def __init__(self, response=""):
        self.response = response
        self.calls = []

    def run(self, args):
        self.calls.append(args)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_argument_arrays_and_non_deleting_copy(tmp_path):
    runner = Runner()
    b = RcloneWebDAVBackend(BackendConfig(upload_remote="up", download_remote="down"), "upload", runner)
    source = tmp_path / "中文 file;$(no).png"
    b.upload(source, "/images/中文 file;$(no).png")
    command = runner.calls[-1]
    assert command[1] == "copyto"
    assert str(source) in command
    assert "up:/images/中文 file;$(no).png" in command
    assert "--ignore-times" in command
    assert not {"sync", "delete", "purge", "--size-only", "--inplace"} & set(command)
    b = RcloneWebDAVBackend(b.config, "download", runner)
    b.download("/a.png", source)
    assert "down:/a.png" in runner.calls[-1]
    assert "--inplace=false" in runner.calls[-1]


def test_metadata_and_missing_only_on_explicit_error():
    runner = Runner(json.dumps(dict(Path="x.png", Size=3, ModTime="", Hashes={})))
    backend = RcloneWebDAVBackend(BackendConfig(), "upload", runner)
    assert backend.get_metadata("/x.png").size == 3
    runner.response = BackendError("missing", code=4)
    assert backend.get_metadata("/x.png") is None
    for code in (1, 5, 7):
        runner.response = BackendError("network", code=code)
        with pytest.raises(BackendError):
            backend.get_metadata("/x.png")
    runner.response = "bad json"
    with pytest.raises(BackendError):
        backend.list_files("/x")


def test_real_subprocess_timeout_cancel_and_error_redaction():
    runner = CommandRunner(2)
    assert runner.run([sys.executable, "-c", "print('ok')"]).strip() == "ok"
    with pytest.raises(BackendError, match="认证") as error:
        runner.run([sys.executable, "-c", "import sys; sys.stderr.write('401 Unauthorized secret=PASSWORD'); sys.exit(1)"])
    assert "PASSWORD" not in str(error.value)
    assert error.value.retryable is False
    with pytest.raises(BackendError, match="未找到"):
        runner.run(["/cloudbridge-does-not-exist"])
    with pytest.raises(BackendError, match="超时"):
        CommandRunner(.1).run([sys.executable, "-c", "import time; time.sleep(30)"])
    stop = threading.Event()
    timer = threading.Timer(.1, stop.set)
    timer.start()
    try:
        with pytest.raises(Cancelled):
            CommandRunner(2, stop).run([sys.executable, "-c", "import time; time.sleep(30)"])
    finally:
        timer.join()


@pytest.mark.parametrize("raw, label", [
    ("response: 401 Unauthorized https://private/?token=PASSWORD", "HTTP 401"),
    ("403 Forbidden Authorization: Bearer PASSWORD", "HTTP 403"),
    ("HTTP/1.1 403 payload PASSWORD", "HTTP 403"),
    ("status code: 401 secret=PASSWORD", "HTTP 401"),
    ("expired_token : refresh token has been used PASSWORD", "expired_token"),
    ("invalid_client PASSWORD", "invalid_client"),
    ("invalid_grant PASSWORD", "invalid_grant"),
])
def test_safe_auth_error_categories(raw, label):
    error = describe_failure(raw, 1)
    assert label in str(error)
    assert "PASSWORD" not in str(error) and "https://" not in str(error)
    assert error.retryable is False and error.code == 1


@pytest.mark.parametrize("raw", [
    'ERROR : run_401.png: connection reset by peer',
    'ERROR : image403.png: request ID 4035: connection reset by peer',
    'ERROR : job_401_403: request ID 123403456: timeout',
])
def test_numbers_in_filenames_do_not_turn_network_errors_permanent(raw):
    error = describe_failure(raw, 5)
    assert error.retryable is True
    assert "HTTP 401" not in str(error) and "HTTP 403" not in str(error)


def test_backend_error_preserves_code_and_labels_operation():
    runner = Runner(BackendError("HTTP 403", retryable=False, code=7))
    backend = RcloneWebDAVBackend(BackendConfig(), "download", runner)
    with pytest.raises(BackendError, match="递归扫描") as error:
        backend.list_files("/private")
    assert error.value.code == 7 and error.value.retryable is False
    assert "/private" not in str(error.value)
    runner.response = Cancelled("cancelled")
    with pytest.raises(Cancelled):
        backend.list_files("/private")
