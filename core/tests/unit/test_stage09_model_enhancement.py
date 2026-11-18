"""5B budget/leakage/drift guards; no real training or generation."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from graphrisk.demo.stage09_evaluation import read_json, write_json
from graphrisk.graph.stage09_enhancement import (
    EnhancementContract, EnhancementLedger, freeze_windows, load_contract,
)
from graphrisk.graph.stage09_enhancement_preflight import input_path, inventory, verify_inventory

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def contract():
    return load_contract(ROOT / "configs/stage09_model_enhancement.json")


@pytest.fixture
def ledger(tmp_path, contract):
    ledger = EnhancementLedger(tmp_path / "ledger.json", contract, "b" * 64)
    ledger.initialize()
    return ledger


def registration(**kwargs):
    return {"input_sha256": "a" * 64, "fold": "fold-1", **kwargs}


def conditional(ledger, name, **kwargs):
    return ledger.reserve("gnn", "conditional", name, registration(appendix_sha256="c" * 64, **kwargs))


@pytest.mark.parametrize("section,key,value", [
    ("metrics", "minimum_xgboost_roc_auc", 0.70),
    ("budgets", "new_paid_calls", 1),
    ("budgets", "old_generation_cap", 200),
    ("budgets", "concurrency", 2),
])
def test_contract_cannot_weaken_confirmed_guards(contract, section, key, value):
    raw = contract.model_dump()
    raw[section][key] = value
    with pytest.raises(ValidationError):
        EnhancementContract.model_validate(raw)


def test_contract_rejects_refunded_confirmation_and_duplicate_candidates(contract):
    raw = contract.model_dump()
    raw["budgets"]["gnn"]["lanes"]["confirmation"] = 8
    raw["budgets"]["gnn"]["lanes"]["search"] = 7
    with pytest.raises(ValidationError, match="reserve"):
        EnhancementContract.model_validate(raw)
    raw = contract.model_dump()
    raw["gnn_candidates"][1] = {**raw["gnn_candidates"][0], "id": "g2"}
    with pytest.raises(ValidationError, match="distinct"):
        EnhancementContract.model_validate(raw)


def test_simultaneous_events_never_cross_window_boundary(contract):
    rows = [(str(i), i // 7) for i in range(140)]
    windows = freeze_windows(rows[::-1], contract)
    for window in windows:
        segments = [window[k] for k in ("train", "early_stop", "calibration", "outer")]
        assert all(a["max_relative_seconds"] < b["min_relative_seconds"] for a, b in zip(segments, segments[1:]))
    assert windows == freeze_windows(rows, contract)
    assert windows[0]["outer"]["end_rank_exclusive"] <= windows[1]["outer"]["start_rank"]


def test_windows_reject_duplicate_ids_and_empty_ties(contract):
    with pytest.raises(ValueError, match="unique"):
        freeze_windows([("same", i) for i in range(100)], contract)
    with pytest.raises(ValueError, match="empty"):
        freeze_windows([(str(i), 10) for i in range(100)], contract)


def test_failed_fit_count_and_abandoned_cap_are_not_refunded(ledger):
    attempt = conditional(ledger, "bad")
    with pytest.raises(ValueError, match="unresolved"):
        conditional(ledger, "next")
    ledger.finish(attempt.name, "abandoned", 0)
    assert ledger.read()["fits"][0]["seconds"] == 3600
    with pytest.raises(ValueError, match="already used"):
        conditional(ledger, "bad")
    second = ledger.reserve("gnn", "failure", "retry", registration(retry_of="bad"))
    ledger.finish(second.name, "failed", 4)
    assert len(ledger.read()["fits"]) == 2


def test_search_order_and_fold_are_guarded(ledger):
    with pytest.raises(ValueError, match="order"):
        ledger.reserve("gnn", "search", "wrong", registration(candidate_id="g2"))
    with pytest.raises(ValueError, match="fold-1"):
        ledger.reserve("gnn", "search", "future", registration(candidate_id="g1", fold="fold-2"))
    fit = ledger.reserve("gnn", "search", "right", registration(candidate_id="g1"))
    ledger.finish(fit.name, "failed", 1)
    with pytest.raises(ValueError, match="order"):
        ledger.reserve("gnn", "search", "same-slot", registration(candidate_id="g1"))


def test_failure_reserve_cannot_be_used_for_extra_candidates(ledger):
    with pytest.raises(ValueError, match="parent"):
        ledger.reserve("gnn", "failure", "extra", registration(retry_of="nonexistent"))
    fit = conditional(ledger, "bad")
    ledger.finish(fit.name, "failed", 1)
    with pytest.raises(ValueError, match="retain"):
        ledger.reserve("gnn", "failure", "changed", registration(retry_of="bad", fold="fold-2"))


def test_wall_clock_keeps_nine_hours_for_confirmation(ledger):
    for i in range(3):
        fit = conditional(ledger, f"slow-{i}")
        ledger.finish(fit.name, "failed", 3600)
    with pytest.raises(ValueError, match="reserve protected"):
        conditional(ledger, "no-development-time")
    ledger.freeze_decision("d" * 64)
    attempt = ledger.reserve("gnn", "confirmation", "final-1", registration(seed=20260921))
    assert attempt.cap == 3600


def test_lane_exhaustion_and_tampering_fail_closed(ledger):
    for i in range(9):
        fit = conditional(ledger, f"fit-{i}")
        ledger.finish(fit.name, "completed", 1)
    with pytest.raises(ValueError, match="quota"):
        conditional(ledger, "tenth")
    altered = read_json(ledger.path)
    altered["fits"] = []
    write_json(ledger.path, altered)
    with pytest.raises(ValueError, match="drift"):
        ledger.read()


def test_deadline_and_invalid_completion_are_rejected(ledger):
    now = [0.0]
    fit = ledger.reserve("gnn", "scale", "time", registration(scale_target=50000), clock=lambda: now[0])
    now[0] = 3600
    with pytest.raises(TimeoutError):
        fit.guard()
    with pytest.raises(ValueError, match="completion"):
        ledger.finish(fit.name, "completed", float("nan"))
    fit.finish("failed")


def test_outer_and_test_access_require_freeze_and_all_confirmations(ledger, contract):
    with pytest.raises(ValueError, match="decision"):
        ledger.open_results("outer")
    ledger.freeze_decision("d" * 64)
    with pytest.raises(ValueError, match="three folds"):
        ledger.open_results("outer")
    with pytest.raises(ValueError, match="fold-1"):
        conditional(ledger, "late-candidate")
    for window in contract.windows:
        for seed in contract.confirmation_seeds:
            fit = ledger.reserve("gnn", "confirmation", f"{window.name}-{seed}", registration(fold=window.name, seed=seed))
            ledger.finish(fit.name, "completed", 1)
    ledger.open_results("outer")
    ledger.open_results("test")
    with pytest.raises(ValueError, match="no repeat"):
        ledger.open_results("test")
    with pytest.raises(ValueError, match="sealed"):
        ledger.reserve("gnn", "failure", "later", registration())


def test_new_ledger_never_overwrites_existing_budget(ledger):
    original = ledger.path.read_bytes()
    with pytest.raises(FileExistsError):
        ledger.initialize()
    assert ledger.path.read_bytes() == original


@pytest.mark.parametrize("path", ["../outside", ".secrets/key", "configs/.env.local", "configs/key.pem"])
def test_secret_or_escape_is_rejected_before_open(tmp_path, path):
    with pytest.raises(ValueError):
        input_path(tmp_path, path)


def test_inventory_binds_both_roots_and_detects_same_size_change(tmp_path):
    source, execution = tmp_path / "source", tmp_path / "execution"
    source.mkdir()
    execution.mkdir()
    (source / "code.py").write_text("AAAA")
    (execution / "model.pt").write_bytes(b"BBBB")
    roots = {"source": source, "execution": execution}
    frozen = inventory(roots, {"source": {"code.py"}, "execution": {"model.pt"}})
    before = (execution / "model.pt").read_bytes()
    verify_inventory(roots, frozen)
    assert (execution / "model.pt").read_bytes() == before
    (source / "code.py").write_text("CCCC")
    with pytest.raises(ValueError, match="input drift:source"):
        verify_inventory(roots, frozen)


def test_duplicate_or_unknown_root_inventory_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        verify_inventory({"source": tmp_path}, [])
    with pytest.raises(ValueError, match="unknown"):
        verify_inventory({"source": tmp_path}, [{"root": "other", "path": "x"}])


def test_slow_fit_cannot_be_reported_completed(ledger):
    fit = conditional(ledger, "slow")
    with pytest.raises(ValueError, match="deadline"):
        ledger.finish(fit.name, "completed", fit.cap)
    ledger.finish(fit.name, "failed", fit.cap + 1)
    assert ledger.read()["fits"][0]["seconds"] == fit.cap + 1


def test_candidate_failure_cannot_reopen_search_after_freeze(ledger):
    fit = conditional(ledger, "failed-search")
    ledger.finish(fit.name, "failed", 1)
    ledger.freeze_decision("d" * 64)
    with pytest.raises(ValueError, match="recovery forbidden"):
        ledger.reserve("gnn", "failure", "retry", registration(retry_of=fit.name))


def test_failed_confirmation_recovery_keeps_seed_and_count(ledger):
    ledger.freeze_decision("d" * 64)
    fit = ledger.reserve("gnn", "confirmation", "bad-final", registration(seed=20260921))
    ledger.finish(fit.name, "failed", 1)
    with pytest.raises(ValueError, match="seed"):
        ledger.reserve("gnn", "failure", "different-seed", registration(retry_of=fit.name, seed=20260928))
    recovery = ledger.reserve("gnn", "failure", "same-seed", registration(retry_of=fit.name, seed=20260921))
    ledger.finish(recovery.name, "completed", 1)
    assert len(ledger.read()["fits"]) == 2


def test_register_rejects_existing_output_before_inspection(tmp_path, monkeypatch):
    from graphrisk.graph import stage09_enhancement_preflight as gate
    if tmp_path.drive.upper() != "E:":
        pytest.skip("E-drive production guard covered on Windows execution root")
    directory = tmp_path / "reports/generated/stage09/route5b/already-exists"
    directory.mkdir(parents=True)
    monkeypatch.setattr(gate, "inspect_environment", lambda *_: pytest.fail("must not inspect/write existing directory"))
    with pytest.raises(ValueError, match="already exists"):
        gate.register(ROOT, tmp_path, "already-exists")


def test_secret_symlink_cannot_bypass_input_gate(tmp_path):
    secret = tmp_path / ".secrets"
    secret.mkdir()
    (secret / "key").write_text("fixture_only")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(secret, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not permit symlink creation")
    with pytest.raises(ValueError, match="secret"):
        input_path(tmp_path, "alias/key")


def test_source_versions_share_budget_and_do_not_refund(tmp_path, contract):
    from graphrisk.agent.stage09_preflight import _content_hash
    from graphrisk.graph.stage09_enhancement_preflight import budget_binding
    root = tmp_path / "project"
    artifact_root = root / contract.artifact_root
    path = artifact_root / "first/fit-ledger.json"
    value = {"schema_version": "stage09-5b-shared-budget-v1", "contract_sha256": _content_hash(contract.model_dump()),
             "ledger_path": str(path.resolve()), "origin_freeze_sha256": "b" * 64}
    write_json(artifact_root / "budget-binding.json", {**value, "content_sha256": _content_hash(value)})
    ledger = EnhancementLedger(path, contract, "b" * 64)
    ledger.initialize()
    fit = conditional(ledger, "failed")
    ledger.finish(fit.name, "failed", 1)
    before = path.read_bytes()
    _, binding = budget_binding(root, contract, artifact_root / "second/fit-ledger.json")
    assert binding["ledger_path"] == str(path.resolve())
    assert path.read_bytes() == before
    other = EnhancementLedger(artifact_root / "second/fit-ledger.json", contract, "c" * 64)
    other.initialize()
    with pytest.raises(ValueError, match="shared active"):
        conditional(other, "reset-quota")


def test_changed_shared_pointer_cannot_reset_quota(tmp_path, contract):
    from graphrisk.agent.stage09_preflight import _content_hash
    from graphrisk.graph.stage09_enhancement_preflight import budget_binding
    artifact_root = tmp_path / contract.artifact_root
    value = {"schema_version": "stage09-5b-shared-budget-v1", "contract_sha256": "wrong",
             "ledger_path": str(artifact_root / "other/fit-ledger.json"), "origin_freeze_sha256": "b" * 64}
    write_json(artifact_root / "budget-binding.json", {**value, "content_sha256": _content_hash(value)})
    with pytest.raises(ValueError, match="never reset"):
        budget_binding(tmp_path, contract, artifact_root / "new/fit-ledger.json")
