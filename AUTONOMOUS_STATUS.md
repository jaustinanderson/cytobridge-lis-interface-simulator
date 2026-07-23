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
| Current phase | `PHASE_3_TASK_BLOCKED` (P3-002 approved and implemented, but blocked on a task-specification conflict; see "Blocked tasks and reasons") |
| Last accepted baseline commit | `eec29e3199996653eb38c3b2d1fa88ee77d64aad` (`main`) |
| Active implementation task branch | `claude/v1.1-p3-002-failure-classification-xub2b4` (BLOCKED) |
| Draft implementation PR | None opened (blocked; see below) |
| Completed-but-unreviewed task count | 0 |
| Autonomous Routine | `DISABLED` |

## Approved and unblocked task IDs

Austin explicitly approved **P3-002 - Structured Failure Classification and
Terminal Queue Initialization**, starting from `main` at commit
`eec29e3199996653eb38c3b2d1fa88ee77d64aad`.

P3-002 is implemented on the active branch (structured classification of the
existing inbound failures plus correct initial TERMINAL queue state for the two
terminal order-state failures, from a single authoritative in-code mapping). It
is **blocked**, not complete: correctly implementing the approved behavior
necessarily changes two pre-existing tests that the task instructions forbid
touching and require to keep passing unchanged. This is an internal
contradiction in the task instructions that only Austin can resolve. No later
Phase 3 implementation task (recovery service, retries, corrected re-drives,
recovery-attempt creation, queue resolution, idempotency) is approved.

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

None. Both permitted completed-but-unreviewed task slots are available. The
P3-002 branch is blocked, not completed, and does not consume a slot.

## Blocked tasks and reasons

**P3-002 - Structured Failure Classification and Terminal Queue Initialization
- BLOCKED pending Austin's decision.**

The P3-002 implementation was written exactly as approved: the failure code is
assigned at each failure site, category and recovery policy are derived from one
authoritative in-code mapping keyed by that code (no duplicated triples, no
inference from the reason string), the twelve recoverable failures initialize
`OPEN`, and `ORDER_FINALIZED` / `ORDER_CANCELLED` initialize `TERMINAL` with a
populated `terminal_at`. A focused new test file
(`tests/test_failure_classification.py`, 20 tests) passes and asserts the frozen
mapping transcribed by hand from the design record.

Correctly implementing that approved behavior, however, changes the observable
result of two pre-existing tests, and the task instructions simultaneously
require those tests to "continue to pass unchanged", forbid modifying existing
tests, and restrict the diff to three files (none of which are those tests).
Those constraints cannot all be satisfied at once:

1. `tests/test_recovery_schema.py::test_existing_inbound_ingestion_still_routes_to_queue`
   (P3-001 schema test) ingests an unmatched-accession message
   (`ORDER_NOT_FOUND`) and asserts `failure_code`, `failure_category`, and
   `recovery_policy` remain `NULL`. P3-002 requirements 1-2 require populating
   exactly those columns at the failure site, so this assertion now fails. This
   test characterized the intermediate P3-001 schema stage where the columns
   were intentionally left null; P3-002 supersedes it.

2. `tests/test_inbound_interfaces.py::test_already_finalized_order_goes_to_error_queue`
   (Session 3 test) finalizes an order, ingests a matching valid message
   (`ORDER_FINALIZED`), and reads the queue through a helper that filters
   `status = 'OPEN'`. P3-002 requirement 8 requires `ORDER_FINALIZED` to
   initialize `TERMINAL`, so the item is no longer `OPEN`, the helper returns an
   empty list, and the test raises `IndexError`.

There is no implementation that both satisfies the approved P3-002 behavior and
leaves these two tests passing unchanged; weakening the implementation to keep
them green would violate the frozen design record (sec 5.1, 6, 8) and P3-002
requirements 1, 2, 7, and 8. Per CLAUDE.md and design record section 13, this
contradiction is returned to Austin rather than resolved autonomously. No draft
PR was opened, because `python -m pytest -q` cannot pass under the task's
current constraints and the task's PR/completion path is a success-only path.

Recommended resolution (requires Austin's explicit authorization, since the two
files are outside the task's authorized-change set - though neither is a frozen
file):

- Authorize updating exactly these two non-frozen existing tests to the
  behavior the frozen design already dictates:
  - In `test_existing_inbound_ingestion_still_routes_to_queue`, replace the
    three `IS NULL` classification assertions with the `ORDER_NOT_FOUND` triple
    (`ORDER_MATCHING` / `RETRY_OR_REDRIVE`).
  - In `test_already_finalized_order_goes_to_error_queue`, read the queue item
    without the `OPEN`-only filter and assert its reason contains "finalized"
    and `status = 'TERMINAL'`.
- Or revise the P3-002 requirement.

## Test evidence (accepted P2-001 corpus)

- PR #13 passed Gate 2 and merged to `main` as
  `681b8295f0555097af0c7b0ae56ee7069ccbcc5a`.
- `python -m pytest -q`: 61 tests passed unchanged on the reviewed head.
- `python -m src.demo_run`: 4 scenarios ran cleanly, exit 0.
- `recovery_corpus.json` parsed with exactly 14 unique approved failure codes.
- Every category, policy, permitted action, expected queue state, and fixture
  reference matched the frozen design.
- Fourteen original fixtures and twelve corrected fixtures were present; the
  two terminal cases correctly had no corrected fixture.

## Test evidence (accepted P3-001 schema task)

- PR #15 passed independent schema review and merged to `main` as
  `dafba1ae2cfe3a8d7e5cad0b5e89926e58dfd90e`.
- `python -m pytest -q`: 90 tests passed (61 existing unchanged plus 29 new
  schema tests); no existing test was modified.
- `python -m src.demo_run`: ran cleanly, exit 0.
- `PRAGMA foreign_key_check`: no violations on a freshly initialized database.
- The accepted PR changed only `schema.sql`,
  `tests/test_recovery_schema.py`, and `AUTONOMOUS_STATUS.md`.

## Test evidence (P3-002 implementation, blocked)

- New file `tests/test_failure_classification.py`: 20 tests pass. They ingest
  all 14 approved original corpus fixtures under their required synthetic
  setups and assert, from a hand-transcribed frozen mapping, that each failed
  ingestion stores exactly one `ERRORED` original message with its exact
  payload, exactly one linked queue item with an exact `raw_payload` copy and
  preserved reason, and the frozen `failure_code` / `failure_category` /
  `recovery_policy`; that the twelve recoverable cases initialize `OPEN` with
  null `resolved_at` / `terminal_at`; that the two terminal cases initialize
  `TERMINAL` with populated `terminal_at` and null `resolved_at` while the
  matched order keeps its status; that all 14 unique approved codes are
  exercised with no null classification field; that both non-integer and
  negative cell counts map to `INVALID_CELL_COUNT`; that valid ingestion still
  files unchanged; and that no FISH result, inbound-filing side effect, or
  recovery-attempt row is produced.
- `python -m pytest -q` on the full suite: 108 passed, 2 failed. The two
  failures are the pre-existing tests described under "Blocked tasks and
  reasons"; they fail because the approved P3-002 behavior supersedes the
  intermediate expectations they encode. No new test fails.
- `python -m src.demo_run`: ran cleanly, exit 0 (unchanged).

## Questions requiring Austin

- **Decide the P3-002 blocker (above).** Either authorize updating the two
  named non-frozen existing tests to the design-dictated behavior, or revise the
  P3-002 requirement. Until then P3-002 cannot reach a green
  `python -m pytest -q` and no draft PR is opened.
- Decide separately when the autonomous Routine may be enabled. It remains
  `DISABLED` unless Austin explicitly authorizes it.

## Next permitted action

Await Austin's decision on the P3-002 blocker. Do not modify existing tests, do
not weaken the P3-002 implementation to force the suite green, do not open a
recovery-service or later task, and do not merge, deploy, release, enable
auto-merge, or push to `main`. **Scheduled routines remain disabled.**
