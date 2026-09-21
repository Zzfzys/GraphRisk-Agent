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
- Local raw data: not present as of 2026-09-21;
- Kaggle account/rules acceptance: requires the user account and has not been verified;
- Download and checksum: pending;
- Fallback dataset path: defined in [synthetic-fallback.md](synthetic-fallback.md).

Stage 01 may begin with the fallback data contract if account access delays the real dataset, but the missing primary dataset must remain visible in the stage status.
