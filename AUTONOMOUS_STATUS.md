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
| Current phase | `PHASE_3_READY_FOR_TASK_APPROVAL` (P3-002 accepted and closed; no follow-on task approved) |
| Last accepted baseline commit | `e6fa627bb0815560e2adf9d0c27b459f129db09e` (`main`) |
| Active implementation task branch | None |
| Draft implementation PR | None |
| Completed-but-unreviewed task count | 0 |
| Autonomous Routine | `DISABLED` |

## Approved and unblocked task IDs

None. P3-002 is complete and accepted; it is no longer active or unreviewed.
No later Phase 3 implementation task is approved. In particular, no recovery
service, retry or corrected re-drive processing, recovery-attempt creation,
queue-transition logic, idempotency, application behavior, or additional
executable test is authorized.

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
| P3-002 - Structured Failure Classification and Terminal Queue Initialization | Independent review passed; PR #17 merged into `main` | `e6fa627bb0815560e2adf9d0c27b459f129db09e` |

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
sequencing. P3-002 subsequently populated them for every existing inbound
failure path while preserving the approved schema nullability.

P3-002 delivered the frozen fourteen-code classification through one
authoritative in-code mapping, initialized twelve recoverable failures as
`OPEN`, and initialized `ORDER_FINALIZED` and `ORDER_CANCELLED` as
`TERMINAL` with `terminal_at`. It preserved the existing reason text,
original messages, raw payloads, successful filing behavior, and order state.
It added no recovery service, retry or corrected re-drive processing,
recovery-attempt write, queue transition after recovery, or idempotency
behavior. Austin explicitly authorized the two non-frozen existing-test updates
recorded below before P3-002 was accepted.

## Completed-but-unreviewed task branches

None. Both permitted completed-but-unreviewed task slots are available.

## Blocked tasks and reasons

None recorded. P3-002 review found no unresolved classification,
compatibility, queue-initialization, or test-scope blocker. The implementation
blocker was resolved through Austin's explicit two-test authorization before
acceptance (see "Blocker resolution").

## Accepted P3-002 scope

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

## Test evidence (accepted P3-002)

- PR #17 passed independent review and merged to `main` as
  `e6fa627bb0815560e2adf9d0c27b459f129db09e`.
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

- Approve, revise, or defer the next separately scoped Phase 3 recovery-service
  task. No later task is approved yet.
- Decide separately when the autonomous Routine may be enabled. It remains
  `DISABLED` unless Austin explicitly authorizes it.

## Next permitted action

Present one bounded Phase 3 recovery-service task for Austin's explicit
approval. **Scheduled routines remain disabled.** No recovery service, retry or
corrected re-drive processing, recovery-attempt write, queue-transition
implementation, idempotency behavior, application change, or additional
executable-test work may begin until its own task ID is approved. Do not merge,
deploy, release, enable auto-merge, or push to `main`.
