from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


CANONICAL_TRANSACTION_COLUMNS = (
    "transaction_id",
    "transaction_time",
    "transaction_amount",
    "card_id",
    "device_id",
    "email_domain",
    "address_id",
    "product_code",
    "is_fraud",
)


class TransactionRecord(BaseModel):
    transaction_id: str = Field(min_length=1)
    transaction_time: datetime
    transaction_amount: float = Field(ge=0)
    card_id: str = Field(min_length=1)
    device_id: str = Field(min_length=1)
    email_domain: str = Field(min_length=1)
    address_id: str = Field(min_length=1)
    product_code: str = Field(min_length=1)
    is_fraud: Literal[0, 1]

    @field_validator("transaction_time")
    @classmethod
    def transaction_time_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("transaction_time must include a timezone")
        return value


def build_identity_key(card_id: str, address_id: str, email_domain: str) -> str:
    canonical = "|".join(
        (card_id.strip().lower(), address_id.strip().lower(), email_domain.strip().lower())
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
