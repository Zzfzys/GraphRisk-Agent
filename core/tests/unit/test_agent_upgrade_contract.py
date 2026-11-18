from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from graphrisk.agent.upgrade_contract import (
    UpgradeContractError, digest, file_hash, load_contract, old_ids, register_budget,
    reviewed_packet, sample_cases,
)
from preflight_agent_upgrade import bind_prior_results, bound, checked_profile, payload_example, readonly_backends

ROOT = Path(__file__).resolve().parents[2]


def contract():
    return load_contract(ROOT / "configs/agent_investigation_experiment.json")


def test_budget_persists_across_restart_without_re_registration_or_reset(tmp_path):
    path = tmp_path / "budget.sqlite3"
    first = register_budget(path, contract())
    assert first == register_budget(path, contract())
    assert first["total_attempts_used"] == 0
    assert first["live_budget_executor_ready"] is False
    # Draft pilot limits can change without minting a new budget.
    changed = contract()
    changed["draft_limits"]["context_tokens"] = 4096
    assert register_budget(path, changed) == first
    changed["experiment_id"] += "-reset"
    with pytest.raises(UpgradeContractError, match="immutable_budget"):
        register_budget(path, changed)
    assert register_budget(path, contract()) == first


def test_attempts_or_deleted_registration_cannot_be_silently_zeroed(tmp_path):
    path = tmp_path / "budget.sqlite3"
    register_budget(path, contract())
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO model_attempts VALUES ('unknown-1','deepseek','unknown',10000)")
    with pytest.raises(UpgradeContractError, match="contains_attempts"):
        register_budget(path, contract())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 1
        db.execute("DELETE FROM budget_registration")
    with pytest.raises(UpgradeContractError, match="no_reset"):
        register_budget(path, contract())


@pytest.mark.parametrize("field,value", [("transaction_id", "private-id"), ("is_fraud", 1),
    ("raw_question", "my card is private-secret"), ("chat_history", "secret"),
    ("identity_key", "secret"), ("path", "E:/private"), ("reviewed", True), ("old_report", {})])
def test_outbound_rejects_extra_fields_without_echoing_input(field, value):
    example = payload_example()
    packet = deepcopy(example["allowed_packet"])
    packet[field] = value
    with pytest.raises(UpgradeContractError) as error:
        reviewed_packet(packet, example["review"], expected_source_sha256=example["review"]["source_bundle_sha256"], approved_review_sha256=example["approved_review_sha256"])
    assert str(error.value) == "outbound_packet_not_registered"


def test_hash_review_rejects_mutated_policy_source_or_self_approved_manifest():
    example = payload_example()
    packet, review = example["allowed_packet"], example["review"]
    kwargs = {"expected_source_sha256": review["source_bundle_sha256"], "approved_review_sha256": example["approved_review_sha256"]}
    assert reviewed_packet(packet, review, **kwargs) == packet
    packet["policies"][0]["excerpt"] = "替换为另一个条款"
    with pytest.raises(UpgradeContractError):
        reviewed_packet(packet, review, **kwargs)
    review["payload_sha256"] = digest(packet)  # Caller recomputing hash is insufficient.
    with pytest.raises(UpgradeContractError):
        reviewed_packet(packet, review, **kwargs)


@pytest.mark.parametrize("mutation", ["nan", "nested_label", "negative_unknown"])
def test_invalid_numbers_and_nested_labels_fail(mutation):
    example = payload_example()
    packet = example["allowed_packet"]
    if mutation == "nan":
        packet["amount"] = float("nan")
    elif mutation == "nested_label":
        packet["history"][0]["is_fraud"] = 1
    else:
        packet["relations"][0]["other_transaction_count"] = -1
    with pytest.raises(UpgradeContractError):
        reviewed_packet(packet, example["review"], expected_source_sha256=example["review"]["source_bundle_sha256"], approved_review_sha256=example["approved_review_sha256"])


def metadata():
    return [{"transaction_id": str(i), "event_second": 1000 + i, "selection_cutoff": 999, "split": "validation" if i < 500 else "test"} for i in range(1000)]


def test_groups_are_disjoint_old_cases_excluded_and_natural_arm_ignores_labels():
    rows = metadata()
    labels = {str(i): i % 2 for i in range(1000)}
    first = sample_cases(rows, labels, {"10", "50"}, seed=20261019)
    flipped = sample_cases(rows, {k: 1-v for k, v in labels.items()}, {"10", "50"}, seed=20261019)
    assert first["natural"] == flipped["natural"]
    assert first["development"] == flipped["development"]
    groups = [{r["transaction_id"] for r in first[g]} for g in ("development", "natural", "diagnostic")]
    assert [len(g) for g in groups] == [15, 30, 30]
    assert len(set.union(*groups)) == 75
    assert not set.union(*groups) & {"10", "50"}
    assert set(first["repeat_ids"]) <= groups[1] | groups[2]
    assert len(set(first["human_read_ids"])) == 5
    assert sum(labels[r["transaction_id"]] for r in first["diagnostic"]) == 15
    assert "is_fraud" not in json.dumps(first["natural"])


@pytest.mark.parametrize("failure", ["equal_time", "duplicate", "no_labels", "too_few"])
def test_sampling_fails_closed(failure):
    rows = metadata()
    labels = {str(i): i % 2 for i in range(1000)}
    if failure == "equal_time": rows[0]["event_second"] = 999
    elif failure == "duplicate": rows.append(rows[0])
    elif failure == "no_labels": labels = {}
    else: rows = rows[:30]
    with pytest.raises(UpgradeContractError):
        sample_cases(rows, labels, set(), seed=1)


def test_old_case_recursion_and_runtime_traversal_guard(tmp_path):
    assert old_ids({"cases": [{"source_id": 7}, {"nested": {"transaction_id": "8"}}]}) == {"7", "8"}
    with pytest.raises(UpgradeContractError, match="outside_runtime"):
        bound(tmp_path, "../outside")


def test_profile_content_hash_is_distinct_from_file_hash_and_both_are_checked(tmp_path):
    path = tmp_path / "profile.json"
    value = {"source": "fixture"}
    content = digest(value)
    value["content_sha256"] = content
    path.write_text(json.dumps(value, indent=2))
    frozen_bytes = file_hash(path)
    assert checked_profile(path, content, frozen_bytes) == value
    assert content != frozen_bytes
    path.write_text(json.dumps(value))  # Same logical content, different frozen bytes.
    with pytest.raises(UpgradeContractError, match="model_profile_changed"):
        checked_profile(path, content, frozen_bytes)


@pytest.mark.parametrize("change", ["live", "budget", "sampling", "limits"])
def test_invalid_experiment_registration_is_rejected(tmp_path, change):
    value = contract()
    if change == "live": value["live_generation_enabled"] = True
    elif change == "budget": value["budget"]["total_attempts"] = 1001
    elif change == "sampling": value["sampling"]["natural_validation"] = 29
    else: value["draft_limits"]["model_calls_per_turn"] = True
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(value))
    with pytest.raises(UpgradeContractError, match="invalid_experiment_contract"):
        load_contract(path)


@pytest.mark.parametrize("tamper", [False, True])
def test_prior_results_are_bound_without_rewriting_or_rescoring(tmp_path, tamper):
    (tmp_path / "results").mkdir()
    bindings = {}
    for index in range(12):
        name = f"case-{index+1:02d}"
        path = tmp_path / "results" / (name + ".json")
        path.write_text('{"fixture":true}')
        bindings[name] = {"sha256": file_hash(path), "bytes": path.stat().st_size}
    value = {"quality": {"joint_pass": 6, "first_denominator": 10, "status": "failed"},
             "ledger": {"global_used": 142, "remaining": 2}, "result_bindings": bindings}
    value["content_sha256"] = digest(value)
    summary = tmp_path / "analysis-summary-1792248734544023100.json"
    summary.write_text(json.dumps(value))
    if tamper:
        (tmp_path / "results/case-01.json").write_text('{"fixture":false}')
        with pytest.raises(UpgradeContractError, match="prior_result_changed"):
            bind_prior_results(tmp_path)
    else:
        assert bind_prior_results(tmp_path)["quality"]["joint_pass"] == 6


def test_backend_preflight_uses_get_only_never_imports_legacy_live_flag(tmp_path, monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        if request.url.path == "/api/tags":
            return __import__("httpx").Response(200, json={"models": [{"name": "qwen3:4b", "digest": contract()["models"]["local_digest"], "size": 1}]})
        if request.url.path == "/api/ps":
            return __import__("httpx").Response(200, json={"models": []})
        return __import__("httpx").Response(200, json={})
    import preflight_agent_upgrade as module
    original = module.httpx.Client
    monkeypatch.setattr(module.httpx, "Client", lambda **kw: original(**kw, transport=module.httpx.MockTransport(handler)))
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: type("Result", (), {"returncode": 1})())
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("GRAPHRISK_STAGE08_LIVE", raising=False)
    (tmp_path / ".env.stage08-deepseek").write_text("DEEPSEEK_API_KEY=fixture-key-not-real\nGRAPHRISK_STAGE08_LIVE=1\n")
    result = readonly_backends(tmp_path, contract())
    assert len(requests) == 4
    assert result["generation_requests"] == 0
    assert result["deepseek"]["readonly_authenticated"] is True
    assert "fixture-key" not in json.dumps(result)
    assert "GRAPHRISK_STAGE08_LIVE" not in __import__("os").environ
