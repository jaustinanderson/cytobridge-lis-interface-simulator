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
| Current phase | `PHASE_3_SCHEMA_IN_REVIEW` (P3-001 completed, awaiting Austin's review) |
| Last accepted baseline commit | `681b8295f0555097af0c7b0ae56ee7069ccbcc5a` (`main`) |
| P3-001 starting `main` commit | `4035e3315c54dd4f9e39df20588a73ef35859e7a` |
| Active implementation task branch | `claude/v1.1-p3-001-recovery-schema` |
| Draft implementation PR | #15 (draft, targets `main`) |
| Completed-but-unreviewed task count | 1 |
| Autonomous Routine | `DISABLED` |

## Approved and unblocked task IDs

Austin explicitly approved exactly one task: **P3-001 - Recovery Data Model and
Schema** (the separately reviewed schema/data-model task). It is now completed
and awaiting Austin's review. No later implementation task is approved: no
failure classification, recovery service, application behavior, or additional
executable test beyond the P3-001 schema tests is authorized.

## Accepted task history

| Task | Accepted outcome | Accepted baseline |
|---|---|---|
| P2-001 - Synthetic Recovery Corpus | Gate 2 passed; PR #13 merged into `main` | `681b8295f0555097af0c7b0ae56ee7069ccbcc5a` |

P2-001 delivered the approved review-only corpus: fourteen original synthetic
AML/MDS FISH failure fixtures, twelve corrected fixtures for recoverable cases,
a machine-readable manifest, and a human-readable guide. It introduced no
schema or recovery implementation.

## Completed-but-unreviewed task branches

One of the two permitted completed-but-unreviewed task slots is in use.

| Task | Branch | Status |
|---|---|---|
| P3-001 - Recovery Data Model and Schema | `claude/v1.1-p3-001-recovery-schema` | Completed; draft PR #15 open; awaiting Austin's review |

P3-001 implements only the approved v1.1 recovery database shape and
database-level constraints. It adds the `interface_error_queue` classification
columns (`failure_code`, `failure_category`, `recovery_policy`), the expanded
`OPEN`/`RESOLVED`/`TERMINAL` status set, `terminal_at`, and the enforced
state/timestamp consistency rules; it creates the `interface_recovery_attempt`
table with the exact approved logical fields and its database enforcement
(unique `request_id`, at most one `SUCCEEDED` attempt per `queue_id`, valid
foreign keys, enumerated `action`/`outcome`, and outcome-to-resulting-message
rules). Per the schema-task sequencing rule the classification columns remain
nullable while non-null values are constrained to the frozen vocabulary,
because the existing Session 3 ingestion code is not authorized to change in
P3-001. No failure classification, recovery processing, service function,
parser change, migration framework, or application behavior was added.

Changed files: `schema.sql`, the new `tests/test_recovery_schema.py`, and this
status document only.

## Blocked tasks and reasons

None recorded. Gate 2 found no unresolved taxonomy, policy, fixture, or
documentation blocker after the approved review corrections.

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
- Each original fixture triggered its intended isolated failure in the parser
  consistency check; each corrected fixture filed successfully.
- Case RC-13 used two distinct probes after Gate 2 correction, avoiding
  duplicate-probe overwrite behavior.
- New text was plain ASCII; relative documentation links resolved;
  `git diff --check` was clean.
- The accepted PR changed only the synthetic recovery corpus and
  `AUTONOMOUS_STATUS.md`; no frozen file, schema, application code, workflow,
  or executable test changed.

## Questions requiring Austin

- Review the P3-001 draft PR (schema and schema tests only) and accept, revise,
  or reject it.
- Approve, revise, or defer any later Phase 3 implementation task (failure
  classification, recovery services, application behavior). None is approved yet.
- Decide separately when the autonomous Routine may be enabled. It remains
  `DISABLED` unless Austin explicitly authorizes it.

## Test evidence (P3-001 schema task)

- `python -m pytest -q`: 90 tests passed (61 existing unchanged plus 29 new
  schema tests); no existing test was modified.
- `python -m src.demo_run`: ran cleanly, exit 0.
- `PRAGMA foreign_key_check`: no violations on a freshly initialized database.
- `schema.sql` initializes a fresh database and is safely rerunnable
  (`IF NOT EXISTS` / `INSERT OR IGNORE`); seed data is not duplicated.
- New schema tests independently prove the queue columns, the exact recovery
  attempt fields, valid and invalid state/timestamp combinations, invalid
  non-null failure codes/categories/policies, both foreign keys, duplicate
  `request_id` rejection, the single-`SUCCEEDED`-per-queue invariant, permitted
  multiple `FAILED`/`REJECTED` attempts, invalid actions/outcomes,
  `SUCCEEDED`/`FAILED` requiring a resulting message, `REJECTED` forbidding one,
  repeatable initialization, and unchanged Session 3 ingestion.
- New text is plain ASCII; `git diff --check` is clean. The diff contains only
  `schema.sql`, `tests/test_recovery_schema.py`, and this status document.

## Next permitted action

Await Austin's review of the P3-001 draft PR. **Scheduled routines remain
disabled.** No recovery implementation, failure-classification population,
application change, or further executable-test work may begin until its own
task ID is approved. Do not merge, deploy, release, enable auto-merge, or push
to `main`.
