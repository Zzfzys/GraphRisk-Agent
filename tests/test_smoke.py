import sys


def test_supported_python_version() -> None:
    assert sys.version_info[:2] == (3, 11)


def test_test_runner_is_operational() -> None:
    assert 2 + 2 == 4
