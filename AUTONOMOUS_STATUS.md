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
| Current phase | `PHASE_2_TASK_IN_REVIEW` (P2-001 corpus delivered; Gate 2 pending review) |
| Last accepted baseline commit | `ec97f5b20daab62b0bafcd62d2c650b6e93fb444` (`main`) |
| Active implementation task branch | `claude/v1.1-p2-001-synthetic-recovery-corpus-34dnyj` |
| Draft implementation PR | #13 (draft, targets `main`) |
| Completed-but-unreviewed task count | 1 (of 2 permitted) |
| Autonomous Routine | `DISABLED` |

## Approved and unblocked task IDs

- `P2-001 - Synthetic Recovery Corpus`. **Explicitly approved by Austin.**
  Delivered on this branch as PR #13: a reviewable, entirely synthetic AML/MDS
  FISH recovery corpus covering exactly the 14 approved failure codes. It is a
  review artifact only (sample fixtures plus a JSON manifest and a corpus
  guide); it adds no recovery behavior, schema change, application code, or
  executable test.

No other implementation task is approved. No recovery service, schema change,
or executable test has been authorized.

## Completed-but-unreviewed task branches

| Task | Branch | PR | Gate | Review state |
|---|---|---|---|---|
| P2-001 | `claude/v1.1-p2-001-synthetic-recovery-corpus-34dnyj` | #13 (draft) | Gate 2 (corpus review) | Pending Austin review |

One of the two permitted completed-but-unreviewed branches is in use. Capacity
remains for one more approved task; a second unreviewed branch would reach the
cap and force a status-only run.

## Blocked tasks and reasons

None recorded. Every failure in the corpus maps cleanly to the frozen taxonomy
(design record section 6); no unmappable failure or new semantics were
discovered, so nothing was escalated as a blocker in this run.

## Test evidence (this run, P2-001 corpus)

- `python -m pytest -q`: existing suite passes unchanged (no test files added
  or modified).
- `python -m src.demo_run`: 4 scenarios ran cleanly, exit 0 (unchanged).
- `recovery_corpus.json` parses; it contains exactly 14 unique approved failure
  codes.
- Every case's `failure_category` and `recovery_policy` matches the frozen
  design-record mapping; permitted actions and expected queue status match the
  frozen recovery rules and state model.
- Every referenced original and corrected fixture exists; the two terminal
  cases have no corrected fixture.
- Each original fixture was confirmed (parser consistency check only) to
  trigger its intended failure; each corrected fixture files.
- New text is plain ASCII; relative documentation links resolve;
  `git diff --check` is clean.
- Scope check: the diff contains only the new `sample_messages/recovery/`
  corpus and this `AUTONOMOUS_STATUS.md`. No frozen file, `schema.sql`, `src/`,
  `queries/`, `tests/`, existing sample message, or workflow changed.

## Questions requiring Austin

- Review and accept (or request changes to) the P2-001 recovery corpus in
  PR #13. The expected `failure_code`, `failure_category`, `recovery_policy`,
  TERMINAL queue status, and permitted-action values encode the frozen design's
  intent and are presented for Austin's review, not derived from implemented
  behavior.
- Decide separately when the autonomous Routine may be enabled. It remains
  `DISABLED` unless Austin explicitly authorizes it.

## Next permitted action

Await Austin's review of PR #13. **Scheduled routines remain disabled.** No
recovery implementation, schema work, or executable-test work may begin before
its own task ID is approved. Do not merge, deploy, release, enable auto-merge,
or push to `main`.
