# Stage 03 validation report

> Validation date: 2026-09-23  
> Result: **PASS — Stage 03 acceptance criteria are complete**  
> Default demonstration source: IEEE-CIS Fraud Detection training data

## Delivered behavior

Stage 03 now provides five independently validated, structured investigation tools and a deterministic workflow that does not call an LLM:

```text
get_transaction
  → get_transaction_features
  → get_risk_score
  → get_related_entities
  → find_risky_neighbors (explicitly unavailable until Stage 04)
```

The first four tools return transaction facts, strictly historical features, the Stage 02 tabular model result, and strictly earlier shared-entity evidence. The graph-risk tool preserves its typed interface and returns `unavailable` without inventing neighbors. The workflow produces a deterministic report with evidence bindings and a separate runtime audit. It is read-only and cannot execute an enforcement action.

## Automated verification

| Check | Result |
|---|---:|
| Contract and five tool/workflow suites | 74 passed in 15.41s |
| Full repository suite | 105 passed in 20.21s |
| `uv lock --check --offline` | PASS, 103 packages resolved |
| Python compileall | PASS |
| `uv build --offline` | PASS, sdist and wheel built |

The 74 focused tests cover request normalization, Pydantic round trips, stable error codes, transaction/feature lookup, model/API agreement, source mismatch, missing and corrupt artifacts, all four relation entity types, multi-hit deduplication, equal/future event exclusion, `unknown-*` exclusion, result limits, graph unavailability, workflow ordering, partial failure, deterministic output and audit separation.

## Real IEEE-CIS investigation

The final command was run twice:

```powershell
uv run --offline python scripts\run_investigation.py 2987100 `
  --data-config configs\ieee_cis.yaml `
  --training-config configs\train_baseline_ieee_cis.yaml `
  --relation-limit 3 `
  --report-output reports\generated\stage-03-final-a-report.json `
  --audit-output reports\generated\stage-03-final-a-audit.json
```

The second run used `final-b` output names. Both reports were 5,511 bytes and had the identical SHA-256:

```text
0F259D59C72B1FD8EC2BEE62F5F374E0090D89EAE78701444503287468FF18F7
```

The audit hashes and investigation IDs differed, as intended. Run A contained five contiguous audit events with an observed total tool-call duration of 1,151.4057 ms. The full CLI process took approximately 6.5 seconds in this validation environment; this includes uv startup, configuration and model loading, which are outside the per-call audit durations.

Sanitized audit sample from run A:

| Sequence | Tool | Status | Error code | Duration |
|---:|---|---|---|---:|
| 1 | `get_transaction` | `ok` | — | 164.30 ms |
| 2 | `get_transaction_features` | `ok` | — | 76.49 ms |
| 3 | `get_risk_score` | `ok` | — | 797.44 ms |
| 4 | `get_related_entities` | `ok` | — | 113.13 ms |
| 5 | `find_risky_neighbors` | `unavailable` | `capability_unavailable` | 0.05 ms |

The committed evidence records this minimized sample rather than the generated audit's UUID and timestamps. Full generated JSON remains local and ignored.

| Report section | Verified result |
|---|---|
| Status | `completed` |
| Transaction | ID `2987100`, amount `100.0`, product `H`, source offset `88169` seconds |
| Historical features | 1h/24h/7d counts all `0`; absent amount statistics preserved as `null` |
| Tabular model | `xgboost-v1`, score `0.4616164565086365`, demonstration level `medium`, `probability_calibrated=false` |
| Shared entities | 9 strictly earlier matches; nearest 3 returned with `truncated=true` |
| Graph risk | `unavailable / capability_unavailable`, no fabricated data or evidence |
| Evidence bindings | transaction ID, feature record, transaction/model version, target and returned relation IDs |

The score is not a 46.16% fraud probability. The level only reflects the current 0.30/0.70 demonstration thresholds. Shared address or email values are modeled relationship evidence and do not prove a real identity or fraud.

## Failure recovery evidence

| Scenario | Observed behavior |
|---|---|
| Transaction `999999999` does not exist | Report `not_found`, `transaction_not_found`, one audit event, then stop |
| IEEE-CIS data with synthetic training config | Report `partial`, risk issue `data_source_mismatch`; transaction, features and relations remain `ok`; five audit events |
| Invalid request | Focused test returns `invalid_input` before a data query |
| Model artifact missing/corrupt | Focused test returns `model_artifact_unavailable`; workflow retains other evidence |
| No historical relation | Relation tool returns `ok` with an empty list rather than an error |
| Unexpected tool exception | Workflow converts it to a structured dependency error without exposing path or stack text |

## Data and evidence gates

- Generated report scan: no `is_fraud`, `identity_key` or `transaction_time` field.
- Every returned relation had `seconds_before_target > 0`; equal-time and future fixtures are excluded by tests.
- Transaction-unique `unknown-*` values cannot form relation edges.
- Request source, training source, resolved serving-feature path and champion metadata source are checked before prediction.
- The model result matches the existing Stage 02 `RiskPredictor` and API in focused tests.
- Audit contains tool metadata only; it does not contain transaction IDs, entity values, full rows, secrets or exception text.
- `git ls-files data/raw data/interim data/processed artifacts reports/generated` returned only the three data-directory `.gitkeep` files. Raw IEEE-CIS files, Parquet data, model artifacts and generated reports remain untracked.

## Learning review

Each report field has a distinct origin. Transaction amount, product and entity values come from the canonical transaction record. Window counts and amount statistics come from Stage 01 point-in-time features and exclude current, equal-time and future events. The risk score and level come from the versioned Stage 02 champion. Relation rows come from direct comparisons against strictly earlier transactions. `evidence_bindings` names the producing tool and record IDs; audit fields describe execution rather than business evidence.

Three failure questions now have concrete answers:

1. **If the model file is removed:** risk output becomes `dependency_error / model_artifact_unavailable`; the report is `partial`, while transaction, historical features and relation evidence can still succeed.
2. **If the transaction ID does not exist:** the first tool returns `not_found / transaction_not_found`; the workflow stops because later sections have no valid subject.
3. **If a future transaction is added:** it cannot change the target's existing point-in-time features or relation evidence because both require event time strictly before the target. It may become history only for a later target.

The main remaining concepts to strengthen in later stages are probability calibration, artifact content hashes, graph-model evaluation, and production latency measurement. They are documented limits rather than hidden assumptions.

## Acceptance conclusion and next input

All Stage 03 acceptance criteria passed. Known boundaries remain explicit: graph risk is deferred to Stage 04; model probabilities and risk thresholds are not calibrated; IEEE-CIS calendar dates are artificial; mapped entities are heuristic; model/feature source binding checks config paths and metadata but not an embedded feature-file hash; PostgreSQL and Neo4j are not required for this stage.

Stage 04 receives stable request/result contracts, strict historical relation semantics, a reserved `find_risky_neighbors` interface, deterministic fixtures, evidence bindings, failure states and audit records. The graph implementation must preserve these boundaries and must not introduce validation/test future relationships into training graphs.
