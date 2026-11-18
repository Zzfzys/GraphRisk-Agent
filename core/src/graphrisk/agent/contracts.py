"""The input boundary shared by upcoming Stage 05 retrieval and graph nodes."""

from __future__ import annotations

from datetime import date, datetime
import re

from pydantic import Field, field_validator

from graphrisk.investigation.contracts import DatasetSource, StrictModel, TransactionId


class Stage05Request(StrictModel):
    transaction_id: TransactionId
    dataset_source: DatasetSource
    policy_as_of: date
    relation_limit: int = Field(default=20, strict=True, ge=1, le=50)

    @field_validator("policy_as_of", mode="before")
    @classmethod
    def policy_date_is_explicit(cls, value: object) -> object:
        if isinstance(value, datetime):
            raise ValueError("policy_as_of must be a date, not a transaction timestamp")
        if isinstance(value, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("policy_as_of must use YYYY-MM-DD without a time")
        return value
