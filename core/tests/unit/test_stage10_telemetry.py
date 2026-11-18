from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from graphrisk.demo.stage10_telemetry import Context, MeasurementRunner, Resources
from graphrisk.demo.stage10_measurement import PROTOCOL
from graphrisk.demo.stage09_evaluation import digest


def context(**changes):
    return Context(**({"case_id": "case-01", "experiment_sha256": "a" * 64, "execution_sha256": "b" * 64,
                      "input_sha256": "c" * 64, "profile_sha256": "d" * 64,
                      "scope": "fixture", "component": "tool"} | changes))


def runner(resources=lambda: Resources(process_rss_mib=12., system_available_gib=4., process_scope="client_process"),
           attempts=lambda: None):
    clocks = iter([1., 1.125])
    times = iter([datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 17, tzinfo=UTC) + timedelta(seconds=.125)])
    return MeasurementRunner(clock=lambda: next(clocks), utcnow=lambda: next(times), resources=resources, attempts=attempts)


def test_operation_envelope_binds_versions_time_resources_and_keeps_raw_result_private():
    counts = iter([130, 131]);probe = runner(attempts=lambda: next(counts))
    secret = {"raw_transaction": "CANARY_PRIVATE_RESULT"}
    assert probe.measure(context(), lambda: secret) is secret
    row = probe.records[0]
    assert row.sample.elapsed_ms == 125. and row.generation_requests == 1
    assert row.context.execution_sha256 == "b" * 64
    assert row.sample.protocol_sha256 == digest(PROTOCOL)
    assert row.sample.protocol_sha256 != row.context.experiment_sha256
    assert row.resource_before.process_rss_mib == 12. and row.resource_after.gpu_used_mib is None
    assert row.finished_at_utc - row.started_at_utc == timedelta(seconds=.125)
    assert "CANARY_PRIVATE_RESULT" not in row.model_dump_json()


@pytest.mark.parametrize("exception,outcome", [(httpx.ReadTimeout("CANARY_SECRET"), "timeout"),
                                                (MemoryError("CANARY_SECRET"), "resource_stop"),
                                                (ValueError("CANARY_SECRET"), "transport_error")])
def test_failed_calls_keep_duration_without_leaking_exception_or_swallowing_it(exception, outcome):
    probe = runner()
    def fail(): raise exception
    with pytest.raises(type(exception)):
        probe.measure(context(), fail)
    row = probe.records[0]
    assert row.sample.outcome == outcome and row.sample.elapsed_ms == 125.
    assert row.generation_requests is None and "CANARY_SECRET" not in row.model_dump_json()


def test_unavailable_resources_and_unknown_attempts_are_null_not_zero():
    def missing(): raise OSError("sensitive driver failure")
    probe = runner(resources=missing, attempts=missing)
    probe.measure(context(), lambda: None)
    row = probe.records[0]
    assert row.resource_before is None and row.resource_probe_after == "unavailable"
    assert row.generation_requests is None and row.sample.prompt_tokens is None


def test_agent_context_requires_pinned_model_prompt_schema_and_does_not_accept_raw_fields():
    with pytest.raises(ValidationError): context(scope="real", component="agent")
    with pytest.raises(ValidationError): context(raw_prompt="secret")
    with pytest.raises(ValidationError): Resources(process_rss_mib=float("nan"))
