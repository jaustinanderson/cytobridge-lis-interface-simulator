# Exercise answer keys - instructor material

> **Instructor material. Do not read before attempting the exercises.** These
> files contain the solution commands and the expected outcomes. All data is
> synthetic; no PHI. CytoBridge is a training simulator, not a production LIS,
> certified interface engine, validated medical device, or Epic/Beaker
> implementation.

Analyst-facing instructions live in each exercise's README; see the
[exercise index](../README.md).

## File format

One JSON file per exercise, named `<EXID>.json`:

| Key | Meaning |
|---|---|
| `id` | The exercise id. |
| `solution_steps` | Steps for the analyst CLI, run after the exercise's `setup.json` steps. Each step is `{"argv", "expect_exit", "note"}`. `argv` excludes the program name; `{db}` and `{inputs}` (the exercise's `inputs/` directory) are substituted, and the runner adds `--json`. |
| `expected` | The persisted outcome `exercise verify` checks by querying the database. |
| `question_answers` | Instructor answers to the questions in the exercise README, each with its repository sources. |

`expected` uses these keys (all optional):

- `orders`: per accession, the order `status`, `result_count`, current
  `results` per probe, `report_status` (`null` means no report row),
  `report_finalized_by` (the report row's `finalized_by`, set by `finalize
  --by`; `null` when there is no report row), `audit_action_counts` (number of
  `audit_event` rows with that `action` whose `order_id` is the order), and
  `validation_blocking` (whether the current `validation.validate_order` result
  has an ERROR finding). Each probe in `results` may state the current
  `fish_result` columns `cells_scored`, `cells_abnormal`, `signal_pattern`,
  `interpretation`, and `entered_by`. `signal_pattern` is OBX-5 component 3 of
  the message that filed the result, or the `result-enter --pattern` value.
  `entered_by` is the actor of the filing: `ingest --actor` (default
  `interface`), the recovery request's `--actor` for a retry or re-drive, or
  `result-enter --entered-by`.
- `queue_items`: per original message control id (MSH-10), the stored
  classification and status, the original message status,
  `original_payload_fixture` (the fixture whose file bytes the original
  message payload and the queue `raw_payload` copy must match),
  `conflict_audit_events` (`REQUEST_ID_CONFLICT` audit rows for the queue
  item), and `attempts` in `attempt_id` order. An attempt's `payload_fixture`
  is the fixture whose sha256 must equal the attempt's `payload_sha256` and,
  when a resulting message exists, that message's payload.
- `outbound`: stored OUTBOUND messages per accession and format (`count`),
  and whether every stored message's per-probe values match the current
  `fish_result` rows (`matches_results`). Per probe the verifier compares cells
  scored, cells abnormal, signal pattern, interpretation, percent abnormal
  (recomputed as `round(100 * abnormal / scored, 1)`, as
  `interfaces.collect_report_data` does; HL7 OBX-5 text and FHIR
  `valueQuantity.value`), and the N/A/I flag (HL7 OBX-8 and the FHIR
  interpretation coding code).

Any other key inside `expected`, an order, a result, a queue item, an attempt,
or an outbound entry is reported as a failing check, never silently ignored.
The supported keys are defined by the constants at the top of
[`src/analyst/exercises.py`](../../src/analyst/exercises.py).
- `totals`: exact row counts per table.
- `reconcile_error_count`: number of ERROR reconciliation findings for the
  whole database.

The solution steps use order id 1 and queue id 1. That is valid only because
every exercise's setup starts by initializing a new, empty database.

## Provenance and review status

Every expected value was derived first from the frozen
[design record](../../validation/v1.1-design-record.md) (sec 6 mapping,
sec 7 to sec 9 recovery rules), the frozen
[v1.1 requirements](../../validation/v1.1-requirements.md), the rules in
[`src/validation.py`](../../src/validation.py), the synthetic probe cutoffs in
[`schema.sql`](../../schema.sql), and the audit writes of the existing
services. The values were then confirmed by replaying each exercise through
the existing public services against an in-memory database; they were not
back-filled from an implementation run. The per-probe `signal_pattern` and
`entered_by` values and `report_finalized_by` were likewise derived from the
fixtures and the documented actor rules above (the filing fixture's OBX-5
component 3 or `result-enter --pattern`; `ingest --actor`, the recovery
`--actor`, or `result-enter --entered-by`; `finalize --by`) and then
confirmed by running the exercises.

Several exercises exercise behavior covered by the human-approved invariants
in design record sec 11: original-message immutability (I-01; EX03, also
EX02 and EX07) and duplicate and replay protection (I-02; EX05). These answer
keys are exercise expectations, not Austin-approved invariant fixtures. They
may be cited as I-01 or I-02 evidence only after Austin reviews them against
the design record, as sec 11 requires.

Passing `exercise run` or `run-all` is automated testing of the simulator. It
is not manual UAT and not evidence of Austin's or any analyst's mastery.
