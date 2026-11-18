"""Fail closed when a CI layer silently skips, leaks packages or reaches services."""
import importlib.util
import os
from pathlib import Path
import sys
import zipfile
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("stage09_ci", ROOT/"scripts/stage09_ci.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def test_child_environment_removes_credentials_and_hides_gpu(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-not-a-real-key")
    monkeypatch.setenv("GRAPHRISK_STAGE07_DSN", "fixture-private")
    env = ci.child_env(ROOT, tmp_path)
    assert "DEEPSEEK_API_KEY" not in env and "GRAPHRISK_STAGE07_DSN" not in env
    assert env["CUDA_VISIBLE_DEVICES"] == "-1" and env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert env["TEMP"] == str(tmp_path/"tmp") and str(ROOT/"src") in env["PYTHONPATH"]


@pytest.mark.parametrize("counts", [dict(total=24, passed=23, skipped=1, failed=0, errors=0),
    dict(total=24, passed=23, skipped=0, failed=1, errors=0),
    dict(total=0, passed=0, skipped=0, failed=0, errors=0)])
def test_database_cannot_succeed_with_skips_failures_or_zero_tests(counts):
    with pytest.raises(ValueError): ci.require_tests(counts, minimum=24)


def test_junit_reports_missing_execution_separately(tmp_path):
    path = tmp_path/"tests.xml"
    path.write_text('<testsuites><testsuite><testcase name="a"/><testcase name="b"><skipped message="DSN missing"/></testcase></testsuite></testsuites>')
    result = ci.junit_counts(path)
    assert result["passed"] == 1 and result["skipped"] == 1 and result["skip_reasons"] == ["DSN missing"]


@pytest.mark.parametrize("private", [".env", "data/raw/train_transaction.csv", "graphrisk/model.pt", "graphrisk/backup.dump", "reports/generated/result.json", "../escape.py"])
def test_wheel_rejects_private_or_unexpected_payload(tmp_path, private):
    wheel = tmp_path/"graphrisk_agent-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in ("graphrisk/__init__.py", "api/main.py", private): archive.writestr(name, "fixture")
    with pytest.raises(ValueError): ci.check_wheel(wheel)


def test_ci_network_guard_refuses_model_and_nonlocal_services(monkeypatch):
    spec = importlib.util.spec_from_file_location("ci_guard_for_test", ROOT/"scripts/stage09_ci_pytest.py")
    guard = importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)
    guard._allowed_ports.add(15439)
    guard.guard_address(("127.0.0.1", 15439))
    for address in (("127.0.0.1", 11434), ("example.com", 443), ("127.0.0.1", 8000)):
        with pytest.raises(ConnectionRefusedError): guard.guard_address(address)


def test_workflow_uses_locked_layers_and_safe_artifact_allowlist():
    import yaml
    value = yaml.safe_load((ROOT/".github/workflows/stage09-regression.yml").read_text())
    assert value["permissions"] == {"contents": "read"}
    assert set(value["jobs"]) == {"cpu", "database", "graphrag", "package", "image"}
    for job in value["jobs"].values():
        assert job["timeout-minutes"] > 0
        assert not job.get("continue-on-error")
        for step in job["steps"]:
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv"):
                assert step["with"]["enable-cache"] is False
                assert step["with"]["cache-local-path"] == "${{ github.workspace }}/.uv-cache"
            if str(step.get("uses", "")).startswith("actions/upload-artifact"):
                assert step["with"]["path"].endswith("/public-summary.json")
    text = (ROOT/".github/workflows/stage09-regression.yml").read_text()
    assert "uv sync --locked" in text and "uv run --offline" in text
    assert "--real" not in text and "pull_request_target" not in text


def test_private_socketpair_works_without_allowing_model_port(monkeypatch):
    spec = importlib.util.spec_from_file_location("ci_socketpair_guard", ROOT/"scripts/stage09_ci_pytest.py")
    guard = importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)
    import socket
    monkeypatch.setattr(socket.socket, "connect", guard._connect)
    left, right = guard._socketpair()
    try:
        left.send(b"x")
        assert right.recv(1) == b"x"
        with pytest.raises(ConnectionRefusedError): guard.guard_address(("127.0.0.1", 11434))
    finally:
        left.close(); right.close()


def test_test_owned_ephemeral_listener_is_allowed_but_existing_services_are_blocked(monkeypatch):
    import socket
    spec = importlib.util.spec_from_file_location("ci_fixture_network", ROOT/"scripts/stage09_ci_pytest.py")
    guard = importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)
    monkeypatch.setattr(socket.socket, "bind", guard._bind)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        guard.guard_address(listener.getsockname())
        with pytest.raises(ConnectionRefusedError): guard.guard_address(("127.0.0.1", 11434))


def test_relative_python_keeps_virtualenv_when_child_changes_directory(tmp_path, monkeypatch):
    import venv

    runtime = tmp_path / "selected-runtime"
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(runtime)
    executable = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    monkeypatch.chdir(tmp_path)
    selected = ci.absolute_interpreter(executable.relative_to(tmp_path))
    elsewhere = tmp_path / "report-output"
    elsewhere.mkdir()
    code = (
        "import pathlib,sys; "
        "assert pathlib.Path(sys.prefix) == pathlib.Path(" + repr(str(runtime)) + "); "
        "assert pathlib.Path.cwd() == pathlib.Path(" + repr(str(elsewhere)) + ")"
    )
    # Resolving bin/python's symlink would lose sys.prefix on Linux; leaving it
    # relative would fail to start after cwd changes, as in the first remote run.
    ci.run([selected, "-c", code], cwd=elsewhere, env=os.environ.copy(),
           log=tmp_path / "interpreter-probe.txt")
