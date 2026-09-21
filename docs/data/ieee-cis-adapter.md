# IEEE-CIS adapter

The adapter converts the authenticated IEEE-CIS competition training files into the GraphRisk canonical transaction contract. It processes the source with DuckDB and writes Parquet without loading the complete wide CSV into pandas.

## Inputs and outputs

| Role | Path |
|---|---|
| Transaction source | `data/raw/ieee-cis/train_transaction.csv` |
| Identity source | `data/raw/ieee-cis/train_identity.csv` |
| Canonical transactions | `data/interim/ieee-cis/transactions.parquet` |
| Point-in-time features | `data/interim/ieee-cis/features_all.parquet` |
| Temporal splits | `data/processed/ieee-cis/*.parquet` |

Raw files and generated Parquet files remain outside Git.

## Mapping

| Canonical field | IEEE-CIS source or rule |
|---|---|
| `transaction_id` | `TransactionID` as a string |
| `transaction_time` | configured UTC anchor plus `TransactionDT` seconds |
| `source_transaction_dt_seconds` | unmodified integer `TransactionDT` |
| `transaction_amount` | `TransactionAmt` |
| `card_id` | normalized composite of `card1` through `card6` |
| `device_id` | `DeviceInfo`, falling back to `DeviceType` |
| `email_domain` | `P_emaildomain`, falling back to `R_emaildomain` |
| `address_id` | normalized composite of `addr1` and `addr2` |
| `product_code` | `ProductCD` |
| `is_fraud` | `isFraud` |
| `identity_key` | SHA-256 of card, address, and email entity IDs |

When a graph entity source is missing, the adapter creates a transaction-unique `unknown-*` identifier. This avoids falsely connecting unrelated transactions merely because they share missing data. The fallback is an engineering choice, not a recovered real entity.

## Time semantics

`TransactionDT` is a relative offset and the source does not expose its real calendar origin. The adapter uses `2000-01-01T00:00:00Z` as an explicitly artificial anchor. Source ordering and time differences remain exact; generated calendar dates and day-of-week values are not real event dates and must not be presented as such.

Transactions with the same `TransactionDT` remain simultaneous. Historical windows exclude the current transaction and every transaction at the same timestamp.

## Validation rules

- Required source columns must be present;
- transaction and identity `TransactionID` values must be unique within their tables;
- time, amount, product and label values must pass type and domain checks;
- the left join must retain every training transaction exactly once;
- canonical entity fields and identity keys must be non-null;
- output row and fraud counts must match the source.

The official test files have no `isFraud` target and are not used for offline evaluation. Chronological train, validation and test partitions are all derived from the labeled official training rows.
