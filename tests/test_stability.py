from pathlib import Path

from cloudbridge.core.scanner import LocalFile
from cloudbridge.core.stability import is_stable


def test_requires_two_observations_and_age():
    file = LocalFile(Path("x"), "x", 10, 100_000_000_000)
    assert not is_stable(file, None, 1000, 120)
    previous = dict(observed_size=10, observed_mtime=file.mtime_ns)
    assert not is_stable(file, previous, 200, 120)
    assert is_stable(file, previous, 221, 120)
    previous["observed_size"] = 9
    assert not is_stable(file, previous, 221, 120)
