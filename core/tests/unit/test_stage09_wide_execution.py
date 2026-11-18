"""Read-only evidence checks reject edits, missing budget and resource failures."""
import threading
from types import SimpleNamespace

import pytest

from graphrisk.agent.stage09_preflight import _content_hash, _sha256
from graphrisk.demo.stage09_evaluation import read_json, write_json
from graphrisk.features import wide_execution as execution
from graphrisk.graph import stage09_enhancement_preflight as preflight


def seal(payload):
    return {**payload, "content_sha256": _content_hash(payload)}


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    root = tmp_path
    artifact = root / "artifacts/stage-09/route5b/test"
    directory = root / "reports/generated/stage09/route5b/test"
    registration = root / "reports/generated/stage09/route5b/source"
    for path in (artifact, directory, registration):
        path.mkdir(parents=True)
    freeze = seal({"inventory": [{"root": "source", "path": "config.json"}]})
    write_json(registration / "freeze.json", freeze)
    (artifact / "features.parquet").write_bytes(b"fixture")
    budget = SimpleNamespace(minimum_available_ram_gib=3.0, minimum_disk_free_gib={"E": 10.0})
    contract = SimpleNamespace(resources=budget, artifact_root="artifacts/stage-09/route5b")
    write_json(directory / "resources.json", [{"available_ram_gib": 5.0, "disk_free_gib": {"E": 20.0}}])
    resource_path = directory / "resources.json"
    pointer = root / contract.artifact_root / "budget-binding.json"
    pointer.write_text("fixture")
    manifest = {"status": "passed", "scope": {"new_model_fits": 0, "generation_calls": 0, "paid_calls": 0,
        "outer_label_reads": 0, "test_label_reads": 0, "legacy_pipeline_overwritten": False},
        "artifact_directory": str(artifact), "source_registration": str(registration),
        "source_registration_sha256": freeze["content_sha256"],
        "outputs": {"features.parquet": {"bytes": 7, "sha256": _sha256(artifact / "features.parquet")}},
        "resource_evidence": {"bytes": resource_path.stat().st_size, "sha256": _sha256(resource_path)},
        "summary": {"rows": 6, "raw_fields": 3, "fields": [1, 2, 3]}}
    write_json(directory / "manifest.json", seal(manifest))
    monkeypatch.setattr(execution, "verify_inventory", lambda *args: None)
    monkeypatch.setattr(execution, "load_contract", lambda *args: contract)
    monkeypatch.setattr(preflight, "budget_binding", lambda *args: (pointer, {"ledger_path": "shared-ledger"}))
    return root, directory, artifact, registration, pointer


def test_artifact_check_is_read_only(evidence):
    root, directory, *_ = evidence
    before = {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in root.rglob("*") if p.is_file()}
    assert execution.check_outputs(root, directory)["read_only"] is True
    assert before == {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("mutation,reason", [
    ("output", "output missing/changed"), ("registration", "registration content drift"),
    ("resources", "resource evidence changed"), ("budget", "binding missing"),
    ("scope", "scope/status mismatch"), ("resource_floor", "continuous floor")])
def test_artifact_changes_and_resource_failures_are_rejected(evidence, mutation, reason):
    root, directory, artifact, registration, pointer = evidence
    manifest = read_json(directory / "manifest.json")
    if mutation == "output":
        (artifact / "features.parquet").write_bytes(b"altered")
    elif mutation == "registration":
        frozen = read_json(registration / "freeze.json")
        frozen["inventory"][0]["path"] = "changed.json"
        write_json(registration / "freeze.json", frozen)
    elif mutation in {"resources", "resource_floor"}:
        resource_path = directory / "resources.json"
        write_json(resource_path, [{"available_ram_gib": 2.0, "disk_free_gib": {"E": 20.0}}])
        if mutation == "resource_floor":
            manifest["resource_evidence"] = {"bytes": resource_path.stat().st_size, "sha256": _sha256(resource_path)}
    elif mutation == "budget":
        pointer.unlink()
    else:
        manifest["scope"]["test_label_reads"] = 1
    if mutation in {"scope", "resource_floor"}:
        write_json(directory / "manifest.json", seal({k: v for k, v in manifest.items() if k != "content_sha256"}))
    with pytest.raises(ValueError, match=reason):
        execution.check_outputs(root, directory)
    if mutation == "budget":
        assert not pointer.exists()


def test_final_resource_sample_prevents_false_success(tmp_path, monkeypatch):
    budget = SimpleNamespace(minimum_available_ram_gib=3.0, minimum_disk_free_gib={"E": 10.0})
    monitor = execution.FeatureMonitor(budget, tmp_path / "resources.json", None, 100)
    monkeypatch.setattr("graphrisk.graph.stage09_monitor.resources", lambda *_: {
        "available_ram_gib": 2.0, "disk_free_gib": {"E": 20.0}})
    thread = threading.Thread(target=lambda: None)
    thread.start()
    with pytest.raises(ValueError, match="resource_floor"):
        monitor.finish(thread)
    assert monitor.stop.is_set() and monitor.violation.is_set()
