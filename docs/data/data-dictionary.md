# Canonical transaction data dictionary

Stage 01 uses a small, semantically clear canonical schema. Synthetic data is generated directly in this schema. A future IEEE-CIS adapter will map available source fields into it without claiming that a derived identity is a true customer account.

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `transaction_id` | string | yes | Unique transaction identifier |
| `transaction_time` | timezone-aware timestamp | yes | Event time converted to UTC |
| `transaction_amount` | non-negative float | yes | Transaction amount in synthetic units |
| `card_id` | string | yes | Card-like graph entity identifier |
| `device_id` | string | yes | Device-like graph entity identifier |
| `email_domain` | string | yes | Email-domain graph entity |
| `address_id` | string | yes | Address-like graph entity identifier |
| `product_code` | string | yes | Product category |
| `is_fraud` | 0 or 1 | yes | Training label; never used in historical feature calculation |
| `identity_key` | SHA-256 string | derived | Heuristic derived from card, address and email domain |

## Identity limitation

`identity_key` is a deterministic grouping heuristic. It is useful for exercising historical windows and relational features, but it is not a verified user ID and must never be described as one.

## Time semantics

- Events are ordered by UTC `transaction_time` and then `transaction_id` for deterministic output;
- Historical windows exclude the current transaction;
- Transactions with the same timestamp are treated as simultaneous and cannot observe one another;
- Features are calculated before dataset splitting, but every feature can only access earlier event time;
- Validation and test transactions may use earlier history, including earlier training or validation events;
- Labels never participate in feature calculation.
