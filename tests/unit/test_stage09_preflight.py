"""Contract and freeze regression tests; no real IEEE data or model calls."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from graphrisk.agent.stage09_preflight import (
    Stage09Contract, check_freeze, check_resources, load_contract, safe_input,
)

ROOT = Path(__file__).resolve().parents[2]


def _contract():
    return load_contract(ROOT / "configs/stage09_experiment.json")


def _frozen(tmp_path):
    path = tmp_path / "configs/model.json"
    path.parent.mkdir()
    path.write_text("AAAA", encoding="utf-8")
    payload = {"schema_version": "1.0", "contract": _contract().model_dump(),
               "artifacts": [{"path": "configs/model.json", "bytes": 4,
                              "sha256": hashlib.sha256(b"AAAA").hexdigest()}]}
    return path, _seal(payload)


def _seal(payload):
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {**payload, "content_sha256": hashlib.sha256(raw.encode()).hexdigest()}


def test_contract_budgets_cover_both_phases_and_no_paid_fallback():
    contract = _contract()
    assert contract.agent.max_generation_attempts == 144
    assert len(contract.graph.candidates) == 6
    assert len(set(contract.graph.confirmation_seeds)) == 3
    assert contract.graph.candidate_test_access_allowed is False
    assert contract.agent.paid_calls_allowed is False


@pytest.mark.parametrize("field", ["paid_calls_allowed", "hosted_fallback_allowed"])
def test_contract_cannot_enable_hosted_or_paid_calls(field):
    value = _contract().model_dump()
    value["agent"][field] = True
    with pytest.raises(ValidationError):
        Stage09Contract.model_validate(value)


def test_contract_rejects_duplicate_candidates_or_seeds():
    value = _contract().model_dump()
    value["graph"]["candidates"][1] = value["graph"]["candidates"][0]
    with pytest.raises(ValidationError, match="distinct"):
        Stage09Contract.model_validate(value)
    value = _contract().model_dump()
    value["graph"]["confirmation_seeds"][1] = value["graph"]["confirmation_seeds"][0]
    with pytest.raises(ValidationError, match="distinct"):
        Stage09Contract.model_validate(value)


def test_freeze_detects_same_length_model_change(tmp_path):
    path, frozen = _frozen(tmp_path)
    assert check_freeze(tmp_path, frozen)["status"] == "passed"
    path.write_text("BBBB", encoding="utf-8")
    result = check_freeze(tmp_path, frozen)
    assert result["violations"] == ["artifact_changed:configs/model.json"]


def test_freeze_detects_missing_file(tmp_path):
    path, frozen = _frozen(tmp_path)
    path.unlink()
    assert check_freeze(tmp_path, frozen)["violations"] == ["artifact_missing:configs/model.json"]


def test_manifest_mutation_is_not_silently_rebound(tmp_path):
    _, frozen = _frozen(tmp_path)
    changed = deepcopy(frozen)
    changed["contract"]["policy_as_of"] = "2027-01-01"
    assert check_freeze(tmp_path, changed)["violations"] == ["freeze_content_mismatch"]


@pytest.mark.parametrize("relative", ["../outside", ".secrets/key", "configs/.env", "reports/generated/private.json"])
def test_freeze_rejects_untrusted_paths(tmp_path, relative):
    with pytest.raises(ValueError, match="path_not_allowed"):
        safe_input(tmp_path, relative)


def test_sealed_inventory_cannot_read_secret_paths(tmp_path):
    payload = {"schema_version": "1.0", "contract": _contract().model_dump(),
               "artifacts": [{"path": ".secrets/key", "bytes": 1, "sha256": "0"*64}]}
    assert check_freeze(tmp_path, _seal(payload))["violations"] == ["artifact_path_invalid"]


def test_empty_or_duplicate_inventory_is_rejected(tmp_path):
    _, frozen = _frozen(tmp_path)
    payload = {k: v for k, v in frozen.items() if k != "content_sha256"}
    payload["artifacts"] *= 2
    assert check_freeze(tmp_path, _seal(payload))["violations"] == ["invalid_artifact_inventory"]


@pytest.mark.parametrize("value", [2.99, float("nan"), float("inf"), None, True])
def test_resource_gate_rejects_low_or_invalid_available_memory(value):
    observed = {"available_ram_gib": value, "free_gpu_mib": 6144,
                "gpu_utilization_percent": 0, "disk_free_gib": {"C": 8, "D": 9, "E": 50}}
    assert check_resources(_contract().resources, observed)["status"] == "failed"


def test_resource_gate_detects_gpu_contention_and_low_disk():
    observed = {"available_ram_gib": 5, "free_gpu_mib": 6144,
                "gpu_utilization_percent": 0, "disk_free_gib": {"C": 8, "D": 9, "E": 50}}
    assert check_resources(_contract().resources, observed)["status"] == "passed"
    observed["gpu_utilization_percent"] = 90
    observed["disk_free_gib"]["D"] = 2
    assert set(check_resources(_contract().resources, observed)["violations"]) == {
        "resource_limit:gpu_utilization_percent", "resource_limit:disk_D"}
