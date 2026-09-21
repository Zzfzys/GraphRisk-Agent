# Dataset access and provenance

## Primary dataset

The preferred dataset is the IEEE-CIS Fraud Detection competition dataset hosted by Kaggle:

`https://www.kaggle.com/c/ieee-fraud-detection/data`

Expected primary files include transaction and identity tables joined by `TransactionID`. The training label is `isFraud`, and `TransactionDT` is used for chronological splitting.

## Access procedure

1. Sign in to Kaggle with the account that will be used for the project;
2. Review and accept the competition rules and data license;
3. Download through the Kaggle website or authenticated Kaggle API;
4. Save the archive outside Git under `data/raw/ieee-cis/`;
5. Record file names, sizes and checksums in a local provenance manifest;
6. Never commit the archive, extracted CSV files or Kaggle credentials.

The project must not automate acceptance of competition terms or redistribute the raw data.

## Semantic limitations

- The dataset is organized around transactions and anonymized identity attributes;
- It does not provide a directly interpretable, complete real-world user entity;
- Card, device, email domain and address fields may be represented as graph entities;
- Any derived `identity_key` is a project heuristic and must not be described as a true customer identifier;
- Synthetic business policies and investigation cases must be labeled as synthetic.

## Current verification status

- Official data page: identified;
- Local raw data: five expected competition CSV files are present under `data/raw/ieee-cis/` as of 2026-09-21;
- Schema check: transaction tables contain `TransactionID` and `TransactionDT`; `train_transaction.csv` contains `isFraud`; identity tables contain `TransactionID`;
- Kaggle account/rules acceptance: user-managed and not independently verified by the project;
- Download provenance: file sizes and SHA-256 checksums are stored in ignored local file `data/raw/ieee-cis/provenance.json`;
- Fallback dataset path: defined in [synthetic-fallback.md](synthetic-fallback.md).

The downloaded files pass the availability and top-level schema gate. They are not yet accepted by the canonical feature pipeline because the IEEE-CIS adapter remains to be implemented and tested.

## Canonical adapter boundary

The current Stage 01 ingestion code accepts the canonical transaction schema documented in [data-dictionary.md](data-dictionary.md). Synthetic data is generated directly in that schema. Raw IEEE-CIS files require a dedicated adapter because their fields are anonymized and do not map one-to-one to business entities. The adapter must join the training transaction and identity tables on `TransactionID`, preserve `TransactionDT` ordering, map only defensible entity fields, and document missing-value behavior. The project must not pretend that the canonical or synthetic schema is the original IEEE-CIS schema.

Only `train_transaction.csv` contains the supervised target. The official test files and `sample_submission.csv` are retained for provenance or optional Kaggle inference; they must not be used as a labeled evaluation set. Model development will create chronological train, validation and test partitions from the official training rows.
