from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from graphrisk.data.contracts import TransactionRecord, build_identity_key


def test_identity_key_is_normalized_and_deterministic() -> None:
    first = build_identity_key(" Card-1 ", "ADDRESS-2", "Example.COM")
    second = build_identity_key("card-1", "address-2", "example.com")

    assert first == second
    assert len(first) == 64


def test_transaction_time_requires_timezone() -> None:
    with pytest.raises(ValidationError, match="must include a timezone"):
        TransactionRecord(
            transaction_id="T1",
            transaction_time=datetime(2025, 1, 1),
            transaction_amount=10.0,
            card_id="card-1",
            device_id="device-1",
            email_domain="example.com",
            address_id="address-1",
            product_code="A",
            is_fraud=0,
        )

    valid = TransactionRecord(
        transaction_id="T2",
        transaction_time=datetime(2025, 1, 1, tzinfo=UTC),
        transaction_amount=10.0,
        card_id="card-1",
        device_id="device-1",
        email_domain="example.com",
        address_id="address-1",
        product_code="A",
        is_fraud=0,
    )
    assert valid.transaction_time.tzinfo is UTC
