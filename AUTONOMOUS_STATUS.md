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
| Current phase | `PHASE_3_READY_FOR_TASK_APPROVAL` (P3-001 accepted and closed; no follow-on task approved) |
| Last accepted baseline commit | `dafba1ae2cfe3a8d7e5cad0b5e89926e58dfd90e` (`main`) |
| Active implementation task branch | None |
| Draft implementation PR | None |
| Completed-but-unreviewed task count | 0 |
| Autonomous Routine | `DISABLED` |

## Approved and unblocked task IDs

None. P3-001 is complete and accepted; it is no longer active or unreviewed.
No later Phase 3 implementation task is approved. In particular, no structured
failure-classification population, recovery service, queue-transition logic,
application behavior, or additional executable test is authorized.

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

The classification columns remain nullable as approved for schema-task
sequencing, preserving unchanged Session 3 ingestion until a separate task is
approved to populate them. P3-001 introduced no failure mapping logic,
recovery processing, service function, parser change, migration framework, or
application behavior.

## Completed-but-unreviewed task branches

None. Both permitted completed-but-unreviewed task slots are available.

## Blocked tasks and reasons

None recorded. P3-001 review found no unresolved schema, compatibility, or
database-constraint blocker.

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

## Test evidence (accepted P3-001 schema task)

- PR #15 passed independent schema review and merged to `main` as
  `dafba1ae2cfe3a8d7e5cad0b5e89926e58dfd90e`.
- `python -m pytest -q`: 90 tests passed (61 existing unchanged plus 29 new
  schema tests); no existing test was modified.
- `python -m src.demo_run`: ran cleanly, exit 0.
- `PRAGMA foreign_key_check`: no violations on a freshly initialized database.
- `schema.sql` initialized a fresh database and was safely rerunnable without
  duplicate seed data.
- The new tests independently proved the queue columns, exact recovery-attempt
  fields, queue-state/timestamp rules, approved vocabulary checks, foreign
  keys, unique `request_id`, at most one `SUCCEEDED` attempt per queue,
  permitted repeated `FAILED`/`REJECTED` attempts, action/outcome checks,
  outcome-to-resulting-message rules, repeatable initialization, and unchanged
  Session 3 ingestion.
- CI passed on Python 3.11 and 3.12, including the test suite and demo.
- New text was plain ASCII and `git diff --check` was clean.
- The accepted PR changed only `schema.sql`,
  `tests/test_recovery_schema.py`, and `AUTONOMOUS_STATUS.md`.

## Questions requiring Austin

- Approve, revise, or defer the next separately scoped Phase 3 implementation
  task. No later task is approved yet.
- Decide separately when the autonomous Routine may be enabled. It remains
  `DISABLED` unless Austin explicitly authorizes it.

## Next permitted action

Present one bounded Phase 3 task for Austin's explicit approval. **Scheduled
routines remain disabled.** No failure-classification population, recovery
service, queue-transition implementation, application change, or additional
executable-test work may begin until its own task ID is approved. Do not merge,
deploy, release, enable auto-merge, or push to `main`.
