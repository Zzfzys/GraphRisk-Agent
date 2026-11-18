from datetime import UTC, datetime

from graphrisk.data.synthetic import generate_synthetic_transactions


def test_synthetic_generation_is_reproducible(tmp_path) -> None:
    first_path = tmp_path / "first.csv"
    second_path = tmp_path / "second.csv"
    arguments = {
        "rows": 200,
        "seed": 42,
        "start_time": datetime(2025, 1, 1, tzinfo=UTC),
    }

    first = generate_synthetic_transactions(first_path, **arguments)
    second = generate_synthetic_transactions(second_path, **arguments)

    assert first.row_count == 200
    assert first.fraud_count == second.fraud_count
    assert first_path.read_bytes() == second_path.read_bytes()
