# CytoBridge v1.1 synthetic recovery corpus (task P2-001)

This directory holds a reviewable, entirely synthetic AML/MDS FISH recovery
corpus for CytoBridge v1.1 (Controlled Error-Queue Recovery). It covers exactly
the fourteen approved failure codes from the frozen design record.

**This is a review artifact only. It does not implement recovery behavior.**
It defines failed-message fixtures, corrected-payload fixtures, and the
design-dictated expected metadata for each failure code, so Austin can review
the corpus before any recovery service, schema change, or executable test is
built. No recovery service, schema change, or test is added by this task.

All data is synthetic. There is no PHI and no real patient, laboratory,
instrument, or employer-confidential material. This remains an educational,
HL7-style simulator, not a certified interface engine, validated medical
device, or Epic/Beaker implementation.

## Source of truth

Every expected value in this corpus is transcribed from Austin's approved,
frozen design, never from running the parser and back-filling its output:

- [`validation/v1.1-design-record.md`](../../validation/v1.1-design-record.md)
  - section 6 (failure taxonomy and recovery mapping),
  - section 7 (recoverability rules),
  - section 8 (state model).
- [`validation/v1.1-requirements.md`](../../validation/v1.1-requirements.md)
  (R-020, R-021, R-022, R-023, R-025 through R-030).
- [`validation/v1.1-test-intent.md`](../../validation/v1.1-test-intent.md).

The accepted baseline for this task is `main` at commit `ec97f5b`.

## Layout

```
sample_messages/recovery/
  README.md              this guide
  recovery_corpus.json   machine-readable manifest (one entry per failure code)
  original/              14 original failed HL7-style payload fixtures
  corrected/             12 corrected fixtures (recoverable cases only)
```

Terminal cases (`ORDER_FINALIZED`, `ORDER_CANCELLED`) have an original fixture
but intentionally no corrected fixture, because recovery is prohibited.

## The fourteen approved cases

| Case | Failure code | Category | Policy | Expected queue | Permitted actions | Corrected fixture |
|---|---|---|---|---|---|---|
| RC-01 | EMPTY_MESSAGE | MESSAGE_STRUCTURE | REDRIVE_ONLY | OPEN | REDRIVE_CORRECTED | yes |
| RC-02 | MISSING_REQUIRED_SEGMENT | MESSAGE_STRUCTURE | REDRIVE_ONLY | OPEN | REDRIVE_CORRECTED | yes |
| RC-03 | NO_OBX | MESSAGE_STRUCTURE | REDRIVE_ONLY | OPEN | REDRIVE_CORRECTED | yes |
| RC-04 | MISSING_ACCESSION | ORDER_MATCHING | REDRIVE_ONLY | OPEN | REDRIVE_CORRECTED | yes |
| RC-05 | ORDER_NOT_FOUND | ORDER_MATCHING | RETRY_OR_REDRIVE | OPEN | RETRY_ORIGINAL, REDRIVE_CORRECTED | yes |
| RC-06 | ORDER_FINALIZED | ORDER_STATE | TERMINAL | TERMINAL | (none) | no |
| RC-07 | ORDER_CANCELLED | ORDER_STATE | TERMINAL | TERMINAL | (none) | no |
| RC-08 | SPECIMEN_UNRECOGNIZED | SPECIMEN | REDRIVE_ONLY | OPEN | REDRIVE_CORRECTED | yes |
| RC-09 | SPECIMEN_INCOMPATIBLE | SPECIMEN | REDRIVE_ONLY | OPEN | REDRIVE_CORRECTED | yes |
| RC-10 | MISSING_PROBE_CODE | FISH_RESULT_CONTENT | REDRIVE_ONLY | OPEN | REDRIVE_CORRECTED | yes |
| RC-11 | UNKNOWN_PROBE_CODE | FISH_RESULT_CONTENT | REDRIVE_ONLY | OPEN | REDRIVE_CORRECTED | yes |
| RC-12 | INVALID_CELL_COUNT | FISH_RESULT_CONTENT | REDRIVE_ONLY | OPEN | REDRIVE_CORRECTED | yes |
| RC-13 | ABNORMAL_EXCEEDS_SCORED | FISH_RESULT_CONTENT | REDRIVE_ONLY | OPEN | REDRIVE_CORRECTED | yes |
| RC-14 | INVALID_INTERPRETATION | FISH_RESULT_CONTENT | REDRIVE_ONLY | OPEN | REDRIVE_CORRECTED | yes |

This mapping is exactly the design record's section 6 table. No code, category,
or policy outside that table appears in this corpus.

## How each trigger is isolated

The current inbound parser
([`src/interfaces/inbound_hl7.py`](../../src/interfaces/inbound_hl7.py))
checks a message in a fixed order:

1. parse: empty payload, then missing required segment (MSH/PID/OBR/SPM), then
   no OBX;
2. order match: missing accession (empty OBR-3), then no matching order, then
   order FINALIZED, then order CANCELLED;
3. specimen: unrecognized SPM-4 code, then specimen incompatible with the panel;
4. per-OBX (all-or-nothing): missing probe code (empty OBX-3), then unknown
   probe code, then non-integer cell count, then abnormal exceeds scored, then
   invalid interpretation.

Each original fixture changes exactly one field from an otherwise-valid message
so that the intended check is the first (and only) one that fails. The
representative triggers used are:

- EMPTY_MESSAGE: an empty (zero-byte) payload.
- MISSING_REQUIRED_SEGMENT: the SPM segment is omitted.
- NO_OBX: no OBX segments are present.
- MISSING_ACCESSION: OBR-3 (the accession) is empty.
- ORDER_NOT_FOUND: a valid message whose accession matches no order.
- ORDER_FINALIZED / ORDER_CANCELLED: a valid message matching a synthetic order
  that is in that state.
- SPECIMEN_UNRECOGNIZED: an unknown SPM-4 code (ZZZ).
- SPECIMEN_INCOMPATIBLE: peripheral blood (PB) against the bone-marrow panel.
- MISSING_PROBE_CODE: an empty OBX-3.
- UNKNOWN_PROBE_CODE: probe code NOTAPROBE.
- INVALID_CELL_COUNT: a non-integer cell count (xx).
- ABNORMAL_EXCEEDS_SCORED: 150 abnormal cells against 100 scored.
- INVALID_INTERPRETATION: an interpretation (EQUIVOCAL) outside NORMAL,
  ABNORMAL, and INDETERMINATE.

Each corrected fixture (where one exists) repairs only that single field and is
otherwise identical, so a reviewer can see exactly what a re-drive would change.

## Manifest fields

`recovery_corpus.json` describes every case with these fields:

- `case_id` - stable corpus identifier (RC-01 .. RC-14).
- `failure_code` / `failure_category` / `recovery_policy` - the frozen triple.
- `isolated_failure_trigger` - the single field that makes this the failure.
- `synthetic_setup` - the synthetic database/order state the case assumes.
- `expected_original_message_status` - ERRORED for every case; the original
  failed message is preserved and never updated to FILED.
- `expected_queue_status` - OPEN for recoverable cases, TERMINAL for prohibited
  cases (per the frozen state model).
- `permitted_recovery_actions` - the actions the policy allows.
- `successful_recovery_prerequisite` - what must be true for a permitted action
  to resolve the item.
- `original_payload` / `corrected_payload` - relative fixture paths
  (`corrected_payload` is null for terminal cases).
- `data_that_must_remain_unchanged` - the original message and queue payload
  that any future recovery action must leave immutable.

## Immutability expectations (from the frozen design)

For every case, a future recovery action must leave the original failed
`interface_message` (its `message_id`, `payload`, `control_id`, `status`, and
`created_at`) and the `interface_error_queue.raw_payload` copy byte-for-byte
unchanged. The original message is never updated to FILED; only a newly created
recovery message may reach FILED. For the two terminal cases the matched order
is never reopened, unfinalized, or uncancelled.

## Relationship to the current (unimplemented) behavior

The v1.1 recovery state fields do not exist yet. The current Session-3 inbound
parser stores each failed message as ERRORED and routes it to
`interface_error_queue` with a free-text `reason` and status OPEN. It does not
yet emit `failure_code`, `failure_category`, `recovery_policy`, or the TERMINAL
queue status, and it does not perform any retry or re-drive.

The `failure_code`, `failure_category`, `recovery_policy`, TERMINAL queue
status, and permitted-action values in this corpus are therefore the frozen
design's expected classification for review, not observations of current
behavior. The only way the current parser was used here was as a consistency
check that each original fixture actually triggers its intended reason string
and each corrected fixture files; no expected recovery metadata was taken from
that run.

## What this task deliberately does not do

- No recovery service, retry, or re-drive implementation.
- No schema or data-model change (schema/data-model work is a separate,
  independently reviewed task).
- No executable tests and no changes to `src/`, `queries/`, `tests/`, existing
  sample messages, or workflows.
- No Phase 3 work.

## Provenance

Recovery behavior and safety decisions were designed and approved by Austin.
This corpus was assembled with Claude under bounded task instructions; the
validation evidence is for Austin to review and accept. This project must never
be represented as unaided work or as autonomous output Austin cannot explain.
