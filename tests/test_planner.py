from cloudbridge.core.models import RemoteFile, Status
from cloudbridge.core.planner import needs_upload


def test_upload_planner_does_not_trust_success_without_remote():
    previous = dict(status=Status.SUCCESS, sha256="digest", remote_version="v")
    assert needs_upload(previous, "digest", None, "v", False)
    assert not needs_upload(previous, "digest", RemoteFile("x", 1), "v", False)
    assert needs_upload(previous, "changed", RemoteFile("x", 1), "v", False)
