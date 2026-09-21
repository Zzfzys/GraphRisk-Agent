from __future__ import annotations

import argparse
import csv
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from graphrisk.config import ProjectConfig, load_config
from graphrisk.data.contracts import CANONICAL_TRANSACTION_COLUMNS, TransactionRecord
from graphrisk.logging_config import configure_logging

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerationSummary:
    output_path: Path
    row_count: int
    fraud_count: int


def generate_synthetic_transactions(
    output_path: Path,
    *,
    rows: int,
    seed: int,
    start_time: datetime,
) -> GenerationSummary:
    if rows < 100:
        raise ValueError("synthetic dataset must contain at least 100 rows")
    if start_time.tzinfo is None or start_time.utcoffset() is None:
        raise ValueError("synthetic start_time must include a timezone")

    random_generator = random.Random(seed)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    email_domains = [f"domain-{index:02d}.example" for index in range(20)]
    product_codes = ["A", "B", "C", "D", "E"]
    profile_count = max(100, rows // 15)
    profiles = [
        {
            "card_id": f"card-{index:05d}",
            "device_id": f"device-{index % max(80, profile_count // 2):05d}",
            "email_domain": email_domains[index % len(email_domains)],
            "address_id": f"address-{index % max(60, profile_count // 3):05d}",
        }
        for index in range(profile_count)
    ]
    risky_devices = {f"device-risk-{index:02d}" for index in range(5)}
    risky_cards = {f"card-{index:05d}" for index in range(5)}

    current_time = start_time.astimezone(UTC)
    fraud_count = 0

    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CANONICAL_TRANSACTION_COLUMNS)
        writer.writeheader()

        for index in range(rows):
            current_time += timedelta(seconds=random_generator.randint(15, 600))
            profile = profiles[random_generator.randrange(profile_count)]
            device_id = profile["device_id"]
            if random_generator.random() < 0.04:
                device_id = random_generator.choice(sorted(risky_devices))

            amount = round(max(0.5, random_generator.lognormvariate(3.7, 0.9)), 2)
            fraud_probability = 0.006
            if device_id in risky_devices:
                fraud_probability += 0.22
            if profile["card_id"] in risky_cards:
                fraud_probability += 0.10
            if amount >= 500:
                fraud_probability += 0.12
            if current_time.hour < 5:
                fraud_probability += 0.015

            is_fraud = int(random_generator.random() < min(fraud_probability, 0.80))
            fraud_count += is_fraud

            record = TransactionRecord(
                transaction_id=f"T{index + 1:08d}",
                transaction_time=current_time,
                transaction_amount=amount,
                card_id=profile["card_id"],
                device_id=device_id,
                email_domain=profile["email_domain"],
                address_id=profile["address_id"],
                product_code=random_generator.choice(product_codes),
                is_fraud=is_fraud,
            )
            row = record.model_dump()
            row["transaction_time"] = record.transaction_time.isoformat()
            writer.writerow(row)

    LOGGER.info(
        "generated synthetic transactions",
        extra={"row_count": rows, "fraud_count": fraud_count, "path": str(output_path)},
    )
    return GenerationSummary(output_path=output_path, row_count=rows, fraud_count=fraud_count)


def generate_from_config(config: ProjectConfig) -> GenerationSummary:
    return generate_synthetic_transactions(
        config.resolve_path(config.paths.raw_transactions),
        rows=config.synthetic.rows,
        seed=config.synthetic.seed,
        start_time=config.synthetic.start_time,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate canonical synthetic transactions")
    parser.add_argument("--config", default="configs/dev.yaml")
    arguments = parser.parse_args()

    config = load_config(arguments.config)
    configure_logging(config.logging.level)
    summary = generate_from_config(config)
    print(
        f"generated {summary.row_count} rows with {summary.fraud_count} fraud labels "
        f"at {summary.output_path}"
    )


if __name__ == "__main__":
    main()
