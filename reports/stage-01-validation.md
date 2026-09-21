# Stage 01 validation report

> Validation date: 2026-09-21
> Data source: deterministic synthetic fallback
> Configuration: `configs/dev.yaml`

## Result

Stage 01 passed its engineering, point-in-time correctness, reproducibility, and artifact gates. These results validate the pipeline mechanics. They do not represent real-world fraud-model performance.

## Commands

```powershell
uv sync --locked --offline
uv run --offline pytest
uv run --offline python -m compileall -q src scripts tests
uv run --offline python scripts\build_features.py --config configs\dev.yaml
```

## Automated tests

- Result: 13 passed;
- Configuration and split-ratio validation;
- Canonical transaction contract and timezone validation;
- Deterministic synthetic data generation;
- Invalid label and duplicate transaction rejection;
- Normalization and Python/SQL identity-key consistency;
- Current transaction exclusion from historical windows;
- Equal timestamp events treated as simultaneous;
- Future transaction insertion cannot alter historical features;
- End-to-end deterministic temporal split.

## Generated dataset

| Metric | Value |
|---|---:|
| Rows | 5,000 |
| Earliest transaction | 2025-01-01 00:04:22 UTC |
| Latest transaction | 2025-01-18 21:42:56 UTC |
| Fraud rows | 97 |
| Fraud rate | 0.019400 |
| Missing transaction amount | 0 |
| Missing identity key | 0 |
| Missing 24-hour historical mean | 2,243 |

The historical mean is intentionally null when an identity has no earlier transaction in the preceding 24 hours.

## Split result

| Split | Rows | Percentage |
|---|---:|---:|
| Train | 3,500 | 70% |
| Validation | 750 | 15% |
| Test | 750 | 15% |

Splits are ordered by `transaction_time` and `transaction_id`. Boundary checks run inside the split function.

## Reproducibility evidence

The pipeline was executed twice with the same configuration. All relevant files retained identical SHA-256 hashes:

| Artifact | SHA-256 |
|---|---|
| Synthetic raw CSV | `6B875255DF5C361653283A15695B8FFA8FB2BAAA07E498AFFAEFC9088386AEB2` |
| All historical features | `5E9FE43FE2CAB5E0873943E4591D6656BE6402DB8942844E9995A43AF8E9C1FC` |
| Train split | `B5BB9DB180CEC942370220B9E4DBF0CC510B8C1914F0B3D097712DC4F19B000B` |
| Validation split | `EFE737FC94F8EA5FC4C8019CEE513779C6DF3F6142226A61CFF8E5914BFE5537` |
| Test split | `374E1B47A98FDA5464451B81DD31BA1B6BC830077A39218BB11FB77A34746287` |

## Git and provenance checks

- Raw CSV, Parquet files, generated reports, secrets and credentials are ignored;
- The canonical data dictionary is versioned;
- The real IEEE-CIS dataset is not present;
- An authenticated IEEE-CIS adapter remains pending until the user accepts the Kaggle rules and downloads the files;
- Synthetic and real-dataset metrics must remain separate.

## Known limitations

- The canonical schema is an engineering contract, not the original IEEE-CIS schema;
- `identity_key` is a grouping heuristic, not a verified customer ID;
- The synthetic distribution is designed for pipeline tests and demonstrations;
- PostgreSQL, Neo4j, Docker and production-scale data processing remain later-stage work.

## Post-stage IEEE-CIS addendum

The primary dataset was downloaded after this original synthetic validation. The adapter and full 590,540-row pipeline now pass; see [the separate IEEE-CIS adapter validation](ieee-cis-adapter-validation.md). The statements above about the dataset being absent describe the original Stage 01 run and are retained as historical evidence.
