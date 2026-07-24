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
| Current phase | `PHASE_3_TASK_IN_REVIEW` (P3-003 implemented; awaiting Austin's review) |
| Last accepted baseline commit | `e6fa627bb0815560e2adf9d0c27b459f129db09e` (`main`) |
| P3-003 starting `main` commit | `2ee1c5b83f26173c4a3e15902411e46fbb47481b` |
| Active implementation task branch | `claude/v1.1-p3-003-recovery-service-core-pxh0fc` |
| Draft implementation PR | #19 (draft, targets `main`) |
| Completed-but-unreviewed task count | 1 (P3-003) |
| Autonomous Routine | `DISABLED` |

## Approved and unblocked task IDs

Austin explicitly approved exactly one task: **P3-003 - Controlled Recovery
Service Core**. It is implemented on branch
`claude/v1.1-p3-003-recovery-service-core-pxh0fc` off `main` at
`2ee1c5b83f26173c4a3e15902411e46fbb47481b` and is now completed but awaiting
Austin's review. No other Phase 3 task is approved: no documentation or UAT
closeout, no hardening, no UI/API/CLI, no deployment or release, and no P3-004
or later work is authorized. The Autonomous Routine remains `DISABLED`.

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

## P3-003 - Controlled Recovery Service Core (completed, awaiting review)

Austin explicitly approved P3-003 and its bounded scope. The task implements the
complete headless recovery-service safety boundary dictated by the frozen design
record (sections 4-11) and its frozen requirements/test-intent files: unchanged
retry, corrected re-drive, recovery-attempt history, eligibility and rejection
rules, original-message immutability, queue resolution and terminalization,
request-id replay and conflict handling, duplicate-filing protection,
handled-failure rollback, transaction-safe persistence, and recovery audit
evidence. No product semantics were invented; every expectation is transcribed
from the frozen files.

### Scope delivered

- `src/recovery.py` (new): the headless service boundary
  `retry_queue_item(conn, queue_id, *, request_id, actor)`,
  `redrive_queue_item(conn, queue_id, corrected_payload, *, request_id, actor)`,
  and `get_recovery_history(conn, queue_id)`, returning a frozen
  `RecoveryAttempt` shape. `request_id` resolution runs before any queue-state or
  action eligibility check: a matching replay (same queue_id, action,
  `payload_sha256`, and actor) returns the recorded attempt and writes nothing
  for prior SUCCEEDED, FAILED, and REJECTED outcomes; any mismatch is exposed as
  a distinct `RequestIdConflictError` (`REQUEST_ID_CONFLICT`) that fabricates no
  recovery attempt and records exactly one `audit_event`. `payload_sha256` is the
  lowercase SHA-256 of the exact UTF-8 request payload: for `RETRY_ORIGINAL` that
  payload is read from the queue item's linked original `interface_message`
  (`interface_message.payload` resolved via `interface_error_queue.message_id`),
  never from `interface_error_queue.raw_payload`, and is the source for both the
  fingerprint and the new retry message; for `REDRIVE_CORRECTED` it is the
  caller-supplied corrected payload. Neither stored copy is rewritten; a null
  original-message link or a missing message row surfaces as a `RecoveryError`
  before any write. After request-id resolution and before ordinary
  queue-state/action eligibility, the stored `failure_code` / `failure_category`
  / `recovery_policy` are validated against the single authoritative mapping in
  `inbound_hl7` (no second mapping); null, contradictory, or unmappable
  classification is a `RecoveryError` blocker that persists no attempt, message,
  result, queue change, order change, or audit event. Classification is never
  inferred from reason strings. Eligibility follows the frozen rules; permitted
  processing reuses the inbound seam and commits success (new FILED message +
  FISH results + filing audit + SUCCEEDED attempt + queue OPEN -> RESOLVED), a
  handled failure (attempted message ERRORED + FAILED attempt, all filing side
  effects rolled back, queue left OPEN), a rejection (single REJECTED attempt,
  plus the approved dynamic OPEN -> TERMINAL when processing establishes the
  target order is now FINALIZED/CANCELLED), or a request-id conflict (audit event
  only) atomically. The rollback boundary spans the entire permitted request --
  message creation, validation, filing, the FILED update, SUCCEEDED-attempt
  insertion, queue resolution, terminalization/handled-failure bookkeeping, and
  the final commit -- so any unexpected (non-inbound) failure at any stage rolls
  the whole request back, re-raises, and leaves `conn.in_transaction` false; such
  failures and database errors are never converted to FAILED, and handled
  `InboundError` semantics are unchanged.
- `src/db.py`: added a keyword-only `commit=True` control to `execute`; the
  default preserves every existing caller. Recovery uses the non-committing path
  under one explicit transaction with a savepoint.
- `src/workflow.py`: threaded the same `commit` control through `record_audit`
  and `enter_fish_result` only (no laboratory-workflow semantics changed).
- `src/interfaces/__init__.py`: threaded `commit` through `store_message`.
- `src/interfaces/inbound_hl7.py`: private, behavior-preserving refactor only.
  Extracted `_store_inbound_message` and `_validate_inbound` as the narrow shared
  seam and threaded `commit` through `_update_message` and `_file_results`.
  `IngestResult`, `ingest_message`/`ingest_file` signatures and behavior, the
  authoritative fourteen-code mapping, and normal success/error-queue routing are
  unchanged. Recovery does not call the legacy ingest path and never creates a
  second `interface_error_queue` item.
- `tests/test_recovery_service.py` (new): 54 executable tests transcribed from
  the frozen design and test-intent, covering all twelve corrected re-drives, the
  ORDER_NOT_FOUND unchanged retry, RETRY rejection for every REDRIVE_ONLY class,
  terminal-item rejection, the dynamic OPEN -> TERMINAL case for currently
  FINALIZED and CANCELLED orders, handled-failure and later-success, mid-operation
  rollback, unexpected-failure rollback and re-raise, invariants I-01 and I-02,
  REQUEST_ID_CONFLICT per mismatch dimension (queue_id, action, payload_sha256,
  actor) and conflict-before-eligibility, matching replay of prior FAILED and
  REJECTED attempts, recovery-history ordering with conflict exclusion,
  file-backed durability after success and after a handled failure with no
  dangling transaction, and PRAGMA foreign_key_check emptiness across every
  outcome. The review-response amendment adds eight tests: an unexpected failure
  after filing but before the success commit rolls the whole request back;
  null / contradictory-category / contradictory-code-policy classification each
  block and persist nothing; request-id replay and conflict are resolved before
  classification validation; and RETRY sources its payload from the linked
  original message (proven against a tampered `raw_payload`) with a null link and
  a dangling link both surfaced as blockers.

No schema, query, frozen document, sample message, corpus manifest, existing
test, `src/demo_run.py`, workflow, or CI file was changed. No new table, column,
index, migration, failure code, category, policy, queue state, or attempt
outcome was added.

## Review response (draft PR #19)

Independent review found three blockers, all fixed on this branch by changing
only `src/recovery.py`, `tests/test_recovery_service.py`, and this document:

1. **Complete transaction rollback.** The unexpected-error rollback boundary in
   `_process` now wraps the entire permitted request (message creation through
   the final commit, including SUCCEEDED-attempt insertion, queue resolution, and
   terminalization/handled-failure bookkeeping). Any unexpected exception at any
   stage rolls the whole request back, re-raises, and leaves
   `conn.in_transaction` false. Handled `InboundError` semantics are preserved and
   generic/database errors are still never converted to FAILED. A new test injects
   a failure during queue resolution (after filing) and proves messages, FISH
   results, attempts, RESULT_ENTERED / INBOUND_RESULT_FILED events, order status,
   queue status/timestamps, and transaction state are all unchanged.
2. **Exact stored classification validated.** After request-id replay/conflict
   resolution but before ordinary queue/action eligibility, `_validate_classification`
   confirms `failure_code`, `failure_category`, and `recovery_policy` are all
   populated and form the exact triple in `inbound_hl7`'s existing authoritative
   mapping (no second mapping). Null, contradictory, or unmappable classification
   raises `RecoveryError` and persists nothing. New tests cover null, a category
   mismatch, a code/policy mismatch, and prove request-id handling runs first.
3. **RETRY uses the original interface_message payload.** `RETRY_ORIGINAL` now
   resolves the queue item's linked original `interface_message` and reads
   `interface_message.payload`, using that exact value for both `payload_sha256`
   and the new retry message; it does not read `interface_error_queue.raw_payload`
   and rewrites neither stored copy. A missing link or missing row surfaces as
   `RecoveryError` without writes. New tests prove the linked message is the
   source (against a tampered `raw_payload`) and cover the null-link and
   dangling-link blockers.

## Test evidence (P3-003, awaiting review; review-response amendment)

- `pip install -r requirements-dev.txt`: succeeded.
- `python -m pytest -q`: **164 passed, 0 failed** (110 pre-existing unchanged
  plus 54 recovery-service tests: 46 original plus 8 added for the review
  response).
- `python -m src.demo_run`: ran cleanly, exit 0.
- Both human-approved invariants (I-01, I-02) pass without weakening.
- All twelve corrected re-drives succeed; the ORDER_NOT_FOUND unchanged retry
  succeeds byte-for-byte after a matching order becomes available, sourced from
  the linked original `interface_message.payload`.
- Success, FAILED, REJECTED, matching replay, REQUEST_ID_CONFLICT,
  terminalization, and history behaviors pass; handled-failure rollback,
  unexpected-error rollback (including after filing but before the success
  commit), classification-blocker, and retry-payload-source behaviors pass;
  file-backed durability passes.
- Exactly one successful recovery exists per queue item; no duplicate filing
  event occurs through replay or a post-resolution request; no recovery creates a
  second error-queue item.
- `PRAGMA foreign_key_check` returns no violations after success, failure,
  rejection, replay, conflict, and terminalization scenarios.
- `schema.sql` still initializes a fresh database and reruns safely;
  `recovery_corpus.json` parses and is unmodified from `main`.
- New and changed text is plain ASCII; `git diff --check` is clean.
- The review-response amendment (on top of `ec75ecdb`) changes only
  `src/recovery.py`, `tests/test_recovery_service.py`, and this
  `AUTONOMOUS_STATUS.md`.
- The complete diff from `main` contains only the authorized files: `src/db.py`,
  `src/workflow.py`, `src/interfaces/__init__.py`,
  `src/interfaces/inbound_hl7.py`, `src/recovery.py`,
  `tests/test_recovery_service.py`, and this `AUTONOMOUS_STATUS.md`.

## Completed-but-unreviewed task branches

| Task | Branch | Draft PR | State |
|---|---|---|---|
| P3-003 - Controlled Recovery Service Core | `claude/v1.1-p3-003-recovery-service-core-pxh0fc` | #19 | Completed; awaiting Austin's review |

One of the two permitted completed-but-unreviewed task slots is now in use; one
slot remains available.

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

- Review the P3-003 draft PR and its evidence; accept, request changes, or
  reject. No follow-on task begins until it is reviewed and its own task ID is
  approved.
- Decide separately when the autonomous Routine may be enabled. It remains
  `DISABLED` unless Austin explicitly authorizes it.

## Next permitted action

Wait for Austin's review of the P3-003 draft PR. No documentation-closeout, UAT,
hardening, P3-004, or later implementation work is approved and none may begin
until Austin approves its own task ID. **Scheduled routines remain disabled.**
Do not merge, approve another task, deploy, release, enable auto-merge, or push
to `main`.
