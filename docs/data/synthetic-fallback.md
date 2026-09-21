# Synthetic data fallback

## Purpose

The fallback dataset prevents external data access from blocking engineering work. It exists to validate schemas, time splitting, feature windows, tests, APIs and Agent workflows. It is not evidence of real-world model quality.

## Required schema

The generator should produce semantically clear fields for:

- `transaction_id`;
- `transaction_time`;
- `transaction_amount`;
- `card_id`;
- `device_id`;
- `email_domain`;
- `address_id`;
- `product_code`;
- `is_fraud`.

## Required scenarios

- normal repeated customer activity;
- burst transactions from one device;
- multiple identities sharing a card or device;
- unusually large transaction amounts;
- historical fraud neighbors;
- unseen entities in validation and test periods;
- events exactly on feature-window and split boundaries.

## Reproducibility

- Use a fixed random seed stored in configuration;
- Generate data in chronological order;
- Store generation parameters with the output;
- Keep the generator in source control;
- Keep generated CSV or Parquet files outside Git.

## Exit condition

When IEEE-CIS becomes available, Stage 01 must reuse the same external data contracts where practical. Synthetic evaluation results must never be mixed with real-dataset metrics.
