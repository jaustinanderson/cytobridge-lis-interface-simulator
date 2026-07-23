# Autonomous status

This is the single writable control document autonomous builders may routinely
update. It records live status only; it does not change any frozen decision.
Frozen decisions live in `validation/v1.1-design-record.md`,
`validation/v1.1-requirements.md`, and
`validation/v1.1-test-intent.md`; substantive changes require Austin's
explicit approval.

## Current state

| Field | Value |
|---|---|
| Current phase | `PHASE_3_TASK_COMPLETE_AWAITING_REVIEW` (P3-002 implemented; blocker resolved under Austin's explicit authorization; awaiting Austin's review) |
| Last accepted baseline commit | `eec29e3199996653eb38c3b2d1fa88ee77d64aad` (`main`) |
| Active implementation task branch | `claude/v1.1-p3-002-failure-classification-xub2b4` |
| Draft implementation PR | `PENDING_RECORD` (opened after the amendment commit; number recorded in the following status commit) |
| Completed-but-unreviewed task count | 1 |
| Autonomous Routine | `DISABLED` |

## Approved and unblocked task IDs

Austin explicitly approved **P3-002 - Structured Failure Classification and
Terminal Queue Initialization**, starting from `main` at commit
`eec29e3199996653eb38c3b2d1fa88ee77d64aad`.

P3-002 is complete and awaiting Austin's review. It implements only structured
classification of the existing inbound failures plus the correct initial queue
state for terminal order failures, from a single authoritative in-code mapping.
No later Phase 3 implementation task (recovery service, retries, corrected
re-drives, recovery-attempt creation, queue resolution, idempotency) is
approved.

## Blocker resolution (P3-002)

The P3-002 review surfaced a blocker: correctly implementing the approved
behavior superseded two pre-existing tests that the original task instructions
forbade modifying and required to keep passing unchanged. The contradiction was
returned to Austin rather than resolved autonomously.

**Austin explicitly authorized the two test updates.** Under that
authorization the blocker is resolved by updating exactly two non-frozen
existing tests to the behavior the frozen design already dictates:

1. `tests/test_recovery_schema.py::test_existing_inbound_ingestion_still_routes_to_queue`
   now expects the unmatched-accession item to be an OPEN `ORDER_NOT_FOUND`
   item (`ORDER_MATCHING` / `RETRY_OR_REDRIVE`) with null `resolved_at` and
   `terminal_at`, instead of null classification. Its comment now notes that the
   schema still permits null classification (proven separately by
   `test_null_classification_allowed`, which is unchanged) while P3-002
   populates classification for the existing inbound path.
2. `tests/test_inbound_interfaces.py::test_already_finalized_order_goes_to_error_queue`
   now retrieves the queue item directly by `result.queue_id` rather than
   through the `OPEN`-only `_open_queue` helper, and asserts it exists, its
   reason contains "finalized", and it is `TERMINAL` with populated
   `terminal_at`, null `resolved_at`, and classification `ORDER_FINALIZED` /
   `ORDER_STATE` / `TERMINAL`. The `_open_queue` helper is unchanged because the
   remaining recoverable-failure tests correctly use it.

No other test, `src/interfaces/inbound_hl7.py`,
`tests/test_failure_classification.py`, schema, fixture, corpus, frozen
document, query, or workflow was changed in this continuation.

## Accepted task history

| Task | Accepted outcome | Accepted baseline |
|---|---|---|
| P2-001 - Synthetic Recovery Corpus | Gate 2 passed; PR #13 merged into `main` | `681b8295f0555097af0c7b0ae56ee7069ccbcc5a` |
| P3-001 - Recovery Data Model and Schema | Independent schema review passed; PR #15 merged into `main` | `dafba1ae2cfe3a8d7e5cad0b5e89926e58dfd90e` |

P2-001 delivered the approved review-only corpus: fourteen original synthetic
AML/MDS FISH failure fixtures, twelve corrected fixtures for recoverable cases,
a machine-readable manifest, and a human-readable guide. It introduced no
schema or recovery implementation.

P3-001 delivered only the approved v1.1 recovery database shape and
database-level constraints. It added the `interface_error_queue`
classification columns (`failure_code`, `failure_category`,
`recovery_policy`), expanded queue states to
`OPEN`/`RESOLVED`/`TERMINAL`, added `terminal_at`, and enforced the
approved state/timestamp combinations. It also added the
`interface_recovery_attempt` table with the exact approved logical fields,
foreign keys, request-id uniqueness, the single-success-per-queue invariant,
valid action/outcome values, and outcome-to-resulting-message rules.

The classification columns were left nullable as approved for schema-task
sequencing, preserving unchanged Session 3 ingestion until a separate task was
approved to populate them. P3-002 is that separate task.

## Completed-but-unreviewed task branches

One: `claude/v1.1-p3-002-failure-classification-xub2b4` (P3-002), complete and
awaiting Austin's review. One of the two permitted completed-but-unreviewed
task slots remains available.

## Blocked tasks and reasons

None. The single P3-002 blocker is resolved under Austin's explicit
authorization (see "Blocker resolution").

## P3-002 scope delivered

- `src/interfaces/inbound_hl7.py` (commit `28935b8`, unchanged in this
  continuation): the failure code is assigned at each exact failure site;
  category and recovery policy are derived from one authoritative in-code
  mapping keyed by that code (no duplicated code/category/policy triples and no
  inference from the human-readable reason string). All fourteen existing
  `InboundError` paths map to exactly one approved code; both a non-integer and
  a negative cell count map to `INVALID_CELL_COUNT`. The twelve recoverable
  failures initialize `OPEN` with null `resolved_at`/`terminal_at`;
  `ORDER_FINALIZED` and `ORDER_CANCELLED` initialize `TERMINAL` with a populated
  `terminal_at` and null `resolved_at`. Generic exceptions and database errors
  are not caught or given a fallback classification. Reason text, exception
  behavior, `IngestResult` shape, and successful-ingestion/filing behavior are
  unchanged; no order state, recovery attempt, processing message, filed FISH
  result, or recovery audit event is produced for these failures; the schema is
  not modified and the classification columns are not tightened to NOT NULL.
- `tests/test_failure_classification.py` (commit `28935b8`, unchanged in this
  continuation): 20 focused tests that ingest all fourteen approved original
  corpus fixtures under their required synthetic setups and assert, from a
  frozen mapping transcribed by hand from the design record, the full per-case
  classification, initial queue state, exact original-message and raw-payload
  preservation, and the absence of any filing or recovery side effect.
- The two authorized existing-test updates described under "Blocker
  resolution".

## Test evidence (P3-002, blocker resolved)

- `python -m pytest -q`: 110 passed, 0 failed (the two previously-conflicting
  pre-existing tests now assert the P3-002 behavior; all other tests unchanged).
- `python -m src.demo_run`: ran cleanly, exit 0.
- All fourteen corpus originals retain their frozen classifications; twelve
  queue items initialize `OPEN` and two initialize `TERMINAL` with populated
  `terminal_at`; no `interface_recovery_attempt` row is created.
- `recovery_corpus.json` parses and is unmodified from `main`.
- New and changed text is plain ASCII; `git diff --check` is clean.
- The amendment on top of `28935b8` changes only
  `tests/test_recovery_schema.py`, `tests/test_inbound_interfaces.py`, and
  `AUTONOMOUS_STATUS.md`. The complete branch diff from `main` contains exactly
  `src/interfaces/inbound_hl7.py`, `tests/test_failure_classification.py`,
  `tests/test_recovery_schema.py`, `tests/test_inbound_interfaces.py`, and
  `AUTONOMOUS_STATUS.md`.

## Test evidence (accepted P3-001 schema task)

- PR #15 passed independent schema review and merged to `main` as
  `dafba1ae2cfe3a8d7e5cad0b5e89926e58dfd90e`.
- `python -m pytest -q`: 90 tests passed (61 existing unchanged plus 29 new
  schema tests) at acceptance.
- The accepted PR changed only `schema.sql`,
  `tests/test_recovery_schema.py`, and `AUTONOMOUS_STATUS.md`.

## Questions requiring Austin

- Review and accept (or return) P3-002 on the draft PR.
- Decide separately when the autonomous Routine may be enabled. It remains
  `DISABLED` unless Austin explicitly authorizes it.

## Next permitted action

Await Austin's review of the P3-002 draft PR. Do not merge, deploy, release,
enable auto-merge, push to `main`, begin recovery-service or any later Phase 3
work, or approve another task until Austin acts. **Scheduled routines remain
disabled.**
