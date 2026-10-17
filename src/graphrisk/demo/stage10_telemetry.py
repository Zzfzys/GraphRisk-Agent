"""Operation-level timing envelope; complements, never replaces, the HTTP guard.

This module is a pre-generation route1 source appendix. Its hashes are registered
after the immutable case manifest; neither cases, thresholds nor budget change.
"""
from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Literal

import httpx
import psutil
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from graphrisk.demo.stage10_measurement import MODEL_REVISION, PROTOCOL, Sample
from graphrisk.demo.stage09_evaluation import digest


class Resources(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    process_rss_mib: float | None = Field(default=None, ge=0)
    system_available_gib: float | None = Field(default=None, ge=0)
    gpu_used_mib: float | None = Field(default=None, ge=0)
    process_scope: Literal["client_process", "service_process", "model_process", "not_measured"] = "not_measured"


class Context(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    case_id: str = Field(pattern=r"^case-\d{2}(?:-repeat)?$")
    experiment_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    prompt_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    schema_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    scope: Literal["fixture", "real"]
    component: Literal["agent", "api_read", "tool", "prospective", "history_replay"]
    concurrency: Literal[1, 2] = 1
    workers: Literal[1] = 1
    model_id: Literal["qwen3:4b"] | None = None
    model_revision: Literal[MODEL_REVISION] | None = None

    @model_validator(mode="after")
    def real_agent_versioned(self):
        if self.scope == "real" and self.component == "agent" and any(
                v is None for v in (self.model_id, self.model_revision, self.prompt_sha256, self.schema_sha256)):
            raise ValueError("real_agent_requires_model_prompt_and_schema_bindings")
        return self


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    context: Context
    sample: Sample
    started_at_utc: datetime
    finished_at_utc: datetime
    timing_boundary: Literal["operation_call_to_return_or_raise_excluding_resource_probes"] = "operation_call_to_return_or_raise_excluding_resource_probes"
    resource_before: Resources | None
    resource_after: Resources | None
    resource_probe_before: Literal["measured", "unavailable"]
    resource_probe_after: Literal["measured", "unavailable"]
    generation_requests: int | None = Field(default=None, ge=0)
    exception_family: Literal["none", "timeout", "resource", "error"]

    @field_validator("started_at_utc", "finished_at_utc")
    @classmethod
    def utc_aware(cls, value):
        if value.tzinfo is None or value.utcoffset().total_seconds() != 0:
            raise ValueError("UTC_aware_timestamp_required")
        return value


def client_resources():
    return Resources(process_rss_mib=psutil.Process(os.getpid()).memory_info().rss / 1024**2,
                     system_available_gib=psutil.virtual_memory().available / 1024**3,
                     process_scope="client_process")


class MeasurementRunner:
    """Keep raw return/exception on caller path; emit only whitelisted metadata.

    Bind the actual delegate in route2 before observing results. For a real Agent,
    the attempt counter must read the canonical AppendLedger, not run counts.
    Resource probes only observe; they do not replace the send-time resource gate.
    """
    def __init__(self, *, clock=time.perf_counter, utcnow=lambda: datetime.now(UTC),
                 resources=client_resources, attempts=lambda: None):
        self.clock, self.utcnow, self.resources, self.attempts = clock, utcnow, resources, attempts
        self.records: list[Envelope] = []

    def _probe(self):
        try:
            value = self.resources()
            if not isinstance(value, Resources):
                raise ValueError("invalid_resource_probe")
            return value
        except Exception:
            return None

    def _attempts(self):
        try:
            value = self.attempts()
            return value if type(value) is int and value >= 0 else None
        except Exception:
            return None

    def measure(self, context: Context, operation):
        if not isinstance(context, Context):
            raise ValueError("validated_measurement_context_required")
        before, count_before = self._probe(), self._attempts()
        started_at, started = self.utcnow(), self.clock()
        outcome, family = "ok", "none"
        try:
            return operation()
        except httpx.TimeoutException:
            outcome, family = "timeout", "timeout"
            raise
        except MemoryError:
            outcome, family = "resource_stop", "resource"
            raise
        except Exception:
            outcome, family = "transport_error", "error"
            raise
        finally:
            elapsed = (self.clock() - started) * 1000
            finished_at = self.utcnow()
            after, count_after = self._probe(), self._attempts()
            count = (count_after - count_before if count_before is not None and count_after is not None
                     and count_after >= count_before else None)
            sample = Sample(case_id=context.case_id, input_sha256=context.input_sha256,
                            protocol_sha256=digest(PROTOCOL), scope=context.scope,
                            component=context.component, concurrency=context.concurrency, workers=context.workers,
                            outcome=outcome, elapsed_ms=elapsed)
            self.records.append(Envelope(context=context, sample=sample, started_at_utc=started_at,
                finished_at_utc=finished_at, resource_before=before, resource_after=after,
                resource_probe_before="measured" if before is not None else "unavailable",
                resource_probe_after="measured" if after is not None else "unavailable",
                generation_requests=count, exception_family=family))
