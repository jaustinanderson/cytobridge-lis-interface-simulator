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
  `audit_action_counts` (number of `audit_event` rows with that `action` whose
  `order_id` is the order), and `validation_blocking` (whether the current
  `validation.validate_order` result has an ERROR finding).
- `queue_items`: per original message control id (MSH-10), the stored
  classification and status, the original message status,
  `original_payload_fixture` (the fixture whose file bytes the original
  message payload and the queue `raw_payload` copy must match),
  `conflict_audit_events` (`REQUEST_ID_CONFLICT` audit rows for the queue
  item), and `attempts` in `attempt_id` order. An attempt's `payload_fixture`
  is the fixture whose sha256 must equal the attempt's `payload_sha256` and,
  when a resulting message exists, that message's payload.
- `outbound`: stored OUTBOUND messages per accession and format, and whether
  their per-probe values match the current `fish_result` rows.
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
back-filled from an implementation run.

Several exercises exercise behavior covered by the human-approved invariants
in design record sec 11: original-message immutability (I-01; EX03, also
EX02 and EX07) and duplicate and replay protection (I-02; EX05). These answer
keys are exercise expectations, not Austin-approved invariant fixtures. They
may be cited as I-01 or I-02 evidence only after Austin reviews them against
the design record, as sec 11 requires.

Passing `exercise run` or `run-all` is automated testing of the simulator. It
is not manual UAT and not evidence of Austin's or any analyst's mastery.
