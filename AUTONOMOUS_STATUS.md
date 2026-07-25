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
| Current phase | `P3_005_AUTHORIZATION_PENDING_REVIEW` (Austin authorized the bounded pilot; execution remains blocked until this authorization PR is independently reviewed and merged) |
| Last accepted baseline commit | `2617f71b7efecba9f054230e7e0488de77c41ad2` (`main`, AF-2026-007 status closeout PR #26 merge commit) |
| Active implementation task branch | None |
| Draft implementation PR | None |
| Completed-but-unreviewed task count | 0 |
| Authorized manual pilot | `P3-005` (one dispatch only; blocked until this authorization PR merges) |
| Autonomous Routine | `DISABLED` |
| Audit findings protocol | `ACTIVE` (`AUDIT_FINDINGS.md`; 7 confirmed findings, all `CONTROLLED`; none `CLOSED`) |

## Audit findings control (ACTIVE - independently accepted)

`AUDIT_FINDINGS.md` is the canonical, reviewer-maintained record for material
mistakes, near misses, post hoc corrections, inaccurate evidence, and planning
failures. PR #23 passed independent final review at head
`4ad36e0e73781c32d7a399875b94888ee835541e`; Austin authorized its merge, and
the accepted control entered `main` as
`553e43e209c13bc809c9be0e5f892129fdc4244a`.

Every future approved task specification must require the builder to report
either an `AUDIT CANDIDATE` with evidence and immediate containment, or
`No audit candidate identified`. Claude and other builders flag candidates;
the independent reviewer confirms and logs official findings. Builders do not
edit the ledger unless the task explicitly authorizes that file.

### Finding lifecycle as applied here

The ledger's three post-diagnosis states remain distinct:

- `CORRECTED` - the immediate defect or inaccurate claim is fixed.
- `CONTROLLED` - the preventive rule, check, template, or gate has been added
  and independently accepted. This does not require pilot evidence.
- `CLOSED` - later independent evidence from an approved task or pilot shows
  that the correction and the preventive control operated as intended.

All seven findings are `CONTROLLED`. Independent review, Austin's merge
authorization, and the PR #23 merge supply the control evidence for AF-2026-001
through AF-2026-006. Independent review, Austin's merge authorization, and the
PR #25 merge supply correction and control evidence for AF-2026-007. No finding
is `CLOSED`.

This audit control does not itself approve implementation work. Austin separately
authorized the bounded P3-005 pilot recorded below. That authorization becomes
executable only after this authorization change is independently reviewed and
merged into `main`, followed by one explicit manual dispatch. The Autonomous
Routine remains `DISABLED`.

**Prior closeout audit report:** No audit candidate was identified during the
PR #24 status-only closeout. The later pre-pilot repository sweep confirmed
AF-2026-007; it does not retroactively turn that earlier statement into evidence
that every branch-sensitive repository claim was checked.

## Accepted audit correction - AF-2026-007

The pre-pilot canonical-fact sweep found two stale P3-004 acceptance claims on
accepted `main`: the README still described the closeout as under review, and
the change-control summary/detail still described PR #21 as a draft awaiting
Austin's review and not merged. `AUDIT_FINDINGS.md` records the formal diagnosis,
correction, cause, and stronger prevention rule.

PR #25 passed independent final review at head
`fc1c1b2846522362c5170cfe997bcf877b5f4aeb`; Austin authorized its merge, and
the correction plus stronger state-sweep control entered `main` at
`8620feb24d83955d8ac3755e34a8e63d59ed8690`. AF-2026-007 is therefore
`CONTROLLED`, not `CLOSED`; closure still requires operating evidence from a
later accepted task closeout.

**AF-2026-007 closeout audit report:** No new audit candidate identified. The
closeout checked `P3-004`, `under review`, `awaiting review`, `draft PR`, and
`not merged` across `README.md`, `validation/change-control-log.md`,
`AUDIT_FINDINGS.md`, and this status document. Remaining hits are accepted-state
records, historical diagnosis/control text, or the unrelated
`pending_review.sql` application filename; no residual branch-time current-state
claim remains. This same-closeout check does not supply the later operating
evidence required for `CLOSED`.

## Approved and unblocked task IDs

None while this authorization PR is unmerged.

Austin explicitly authorized **P3-005 - v1.1 Recovery UAT Evidence Pilot** on
2026-07-25. Once this exact authorization change passes independent review and
merges into `main`, P3-005 becomes the sole approved and unblocked task. It may
then be dispatched manually exactly once. The approval does not enable a
scheduled or recurring runner and does not authorize any other task.

### P3-005 task contract

**Purpose:** Execute UAT-011 through UAT-018 exactly as defined in
`validation/uat-test-scripts.md`, capture observed synthetic evidence, and
produce one standalone execution report. This is an evidence-only pilot; it
must not change product behavior or rewrite the approved UAT procedures.

**Authorization state:** `APPROVED_BY_AUSTIN / BLOCKED_PENDING_AUTHORIZATION_PR_MERGE`.

**Execution gate and ordering:**

1. This authorization PR must pass independent review and merge into `main`.
2. A human dispatcher must provide the exact resulting `main` commit SHA and
   manually dispatch P3-005 once.
3. The builder must fetch the remote, confirm `origin/main` equals that exact
   dispatched SHA, confirm no P3-005 branch or PR is already active, and branch
   directly from it as `agent/p3-005-recovery-uat-evidence`.
4. The builder executes the task, opens one draft PR, and stops for independent
   review. It may not merge its own PR.
5. No second task and no recurring runner may start while the P3-005 draft PR is
   unreviewed.

**Only authorized repository changes during execution:**

- Create
  `validation/v1.1-recovery-uat-execution-report.md`.
- Update `AUTONOMOUS_STATUS.md` only to record the exact execution baseline,
  task branch, draft PR/head, completion or blocker state, validation evidence,
  and the required audit-candidate report.

The file-backed `uat_recovery.db`, temporary Python snippets, captured raw
console files, virtual environments, and caches are scratch artifacts only.
They must remain outside the commit and be deleted or excluded before the draft
PR is opened.

**Explicitly prohibited repository changes:**

- No application code, test, schema, query, sample-message, corpus, fixture,
  frozen record, UAT-definition, README, workflow, CI, or dependency change.
- No change to `AUDIT_FINDINGS.md`; the independent reviewer owns official
  findings and lifecycle transitions.
- No helper script, generated database, console log, screenshot, or binary
  artifact may be committed.
- No new product semantic, recovery behavior, failure handling, interface,
  UI/API/CLI, transport, deployment, authentication, or release work.
- No PHI or external clinical system; use only the repository's synthetic data.
- No merge, deploy, release, auto-merge, direct push to `main`, or Autonomous
  Routine enablement.

**Pre-execution baseline:**

- Record the dispatched commit SHA, UTC date/time, tester/builder identity,
  Python version, platform, and dependency-install result in a canonical fact
  sheet at the top of the new report.
- From a clean tree, install `requirements-dev.txt` in an isolated environment.
- Run `python -m pytest -q` and `python -m src.demo_run`. Record the exact
  observed test count and demo scenario count. Either command failing is a stop
  condition; do not begin the UAT sequence.
- Verify `validation/uat-test-scripts.md`,
  `sample_messages/recovery/recovery_corpus.json`, and the frozen v1.1 records
  are unchanged from the dispatched baseline.

**Required UAT execution and evidence:**

- Execute UAT-011, UAT-012, UAT-013, UAT-014, UAT-015A, UAT-015B, UAT-016,
  UAT-017, and UAT-018 in order using their stated preconditions and public
  service calls. Use fresh synthetic state wherever the procedure requires it.
- For every UAT/subcase, record the setup, exact command or snippet used,
  expected result, observed values/rows/counts, evidence comparison, and an
  explicit `PASS` or `FAIL`. A script definition, automated test, or demo
  claim is not manual execution evidence.
- UAT-012 must compare every field named by the immutability claim plus queue
  `raw_payload`; do not infer whole-record immutability from a subset.
- UAT-015B must prove `state["calls"] == 2`, unconditional dependency
  restoration, the first in-transaction write before the injected fault, the
  exact rolled-back and preserved records, and
  `conn.in_transaction is False`.
- The report must include an outcome-to-persisted-record matrix for observed
  `SUCCEEDED`, `FAILED`, `REJECTED`, replay, and
  `REQUEST_ID_CONFLICT` behavior, using exact transaction language.
- Check the recovery portion of `docs/workflow-diagram.md` edge by edge against
  the observed attempt, message, queue, filing-event, and conflict evidence.
  Record the reconciliation in the report; do not edit the diagram.
- UAT-018 must use a file-backed scratch database, verify durability after
  reopen, `conn.in_transaction is False`, and an empty
  `PRAGMA foreign_key_check`, then remove the scratch database.
- Treat every executable UAT snippet as code: deterministic setup,
  unconditional teardown, explicit injection-point evidence, exact vocabulary,
  and an independent dry run are required.

**Audit-control evidence required in the report:**

- AF-2026-001: injection point and pre-fault milestone evidence.
- AF-2026-002: outcome-by-outcome persistence matrix.
- AF-2026-003: edge-by-edge diagram reconciliation.
- AF-2026-004: canonical facts, repository-wide stale-claim search, and
  field-complete immutability comparison.
- AF-2026-005: deterministic snippet setup/teardown, exact transaction
  vocabulary, injection evidence, and dry-run result.
- AF-2026-006: no finding status transition without matching lifecycle evidence;
  the builder may identify candidate evidence but may not close a finding.
- AF-2026-007: branch-sensitive state sweep for `P3-005`, `under review`,
  `awaiting review`, `draft PR`, and `not merged`, covering at least
  `README.md`, `validation/change-control-log.md`, `AUDIT_FINDINGS.md`,
  the execution report, and this status document. List searched terms and
  affected files. Because the task PR is not yet merged, this pre-merge sweep
  alone cannot close AF-2026-007.

The independent reviewer, not the builder, decides whether the pilot supplies
closure evidence for any finding. The builder must not mark any finding
`CLOSED` or imply that executing a check proves the preventive control
effective without review.

**Stop conditions and containment:**

Stop the UAT sequence immediately if any of the following occurs: baseline
mismatch; dirty or conflicting scope; failed baseline test/demo; failed UAT;
ambiguous expected result; observed/evidence mismatch; unavailable evidence;
unrestored instrumentation; dangling transaction; foreign-key violation; need
to alter an unauthorized file; need to invent semantics; or an audit candidate.

On a stop:

1. Preserve only synthetic, non-sensitive evidence.
2. Record the completed steps and exact blocker in the authorized report.
3. Set P3-005 to `BLOCKED` in this status document.
4. Report an `AUDIT CANDIDATE` with evidence and immediate containment when
   applicable.
5. Open at most one draft PR containing only the authorized partial report and
   status update, then stop for Austin and independent review. Do not continue
   later UATs merely to accumulate more results.

**Completion and acceptance criteria:**

- All nine named UAT executions/subcases pass with direct observed evidence.
- The baseline suite and demo pass, and the post-execution suite and demo pass
  with their exact counts recorded.
- The canonical fact sheet, persistence matrix, diagram reconciliation,
  immutable-field comparison, UAT-015B injection/cleanup evidence, UAT-018
  durability/FK evidence, and branch-sensitive sweep are complete.
- The execution report clearly separates observed manual evidence, automated
  evidence, and inference. Unsupported claims are prohibited.
- Repository scope is exactly the new report plus this status document.
- `git diff --check` is clean; both changed files are plain ASCII; all relative
  Markdown links resolve; no scratch artifact is tracked.
- The branch is directly based on the dispatched `main` SHA and is not behind
  it when the draft PR opens.
- The builder reports exactly one of:
  - `AUDIT CANDIDATE: <diagnosis, evidence, containment, decision needed>`; or
  - `No audit candidate identified`.
- One draft PR is opened with the exact baseline, head SHA, scope, validation,
  UAT result table, and audit report in its description. The builder then stops.
- P3-005 is not accepted or complete merely because the builder reports PASS.
  Independent review and Austin's later merge authorization remain required.

No hardening or other Phase 3 work is approved. The Autonomous Routine remains
`DISABLED`.

## Accepted P3-004 scope

P3-004 is a documentation and validation closeout for the accepted controlled
recovery implementation (P3-001 through P3-003). The **only executable change**
is adding a deterministic synthetic recovery demonstration to `src/demo_run.py`
through the existing public recovery service; no product semantics were invented.

**Files changed (authorized only):**

- `src/demo_run.py` - added scenario 5, a controlled-recovery demonstration
  through the public service (`retry_queue_item` / `redrive_queue_item` /
  `get_recovery_history` / `RequestIdConflictError`): corrected re-drive,
  unchanged ORDER_NOT_FOUND retry, handled failure then later success, and
  duplicate/replay/`REQUEST_ID_CONFLICT` protection. Scenario count updated
  four -> five. No private helper is called and no attempt/queue state is written
  by hand.
- `validation/traceability-matrix.md` - R-020 - R-041 mapped to implementing
  file/function or schema constraint, executable test, and applicable UAT;
  automated `PASS` separated from manual `DEFINED`; totals updated to 41.
- `validation/uat-test-scripts.md` - UAT-011 - UAT-018 added (public service
  only, no manual queue `UPDATE`); UAT-001 - UAT-010 preserved; summary updated.
- `docs/interface-troubleshooting.md` - rewritten to the controlled recovery
  workflow (raw SQL now read-only).
- `docs/workflow-diagram.md` - compact recovery view added.
- `docs/demo-script.md` - updated to the five-scenario demo with a recovery
  segment.
- `validation/validation-summary.md`, `validation/known-issues.md` (KI-03 moved
  to resolved), `validation/risk-assessment.md` (recovery risks RA-17 - RA-22),
  `validation/change-control-log.md` (v1.1 P2-001/P3-001..P3-004 history).
- `README.md`, `docs/portfolio-review.md`, `docs/hiring-manager-review.md` - v1.1
  framing, corrected figures, provenance statement, roadmap update.
- `AUTONOMOUS_STATUS.md` - this status update.

**No other file changed.** No schema, query, sample message, corpus, frozen file,
CI/workflow file, `src/recovery.py` or any application module other than
`src/demo_run.py`, and no existing or new test was modified. Public signatures and
recovery semantics are unchanged.

**Verified figures:** 164 pytest tests pass across eight suites; `python -m
src.demo_run` exits 0 with five scenarios and every printed claim matching
persisted state; 41 requirements traced; UAT-001 - UAT-018 present; manual UAT is
defined, not claimed executed; `git diff --check` clean; new/changed text is
plain ASCII; `recovery_corpus.json` parses and is unchanged.

**Status:** P3-004 passed independent final review. Austin accepted the closeout
and authorized merge of PR #21, which merged into `main` as
`406509ad2847efdf5dc6a09f7f6de52e3dfb514b`.

## P3-004 review response (PR #21)

Independent review of PR #21 found documentation/validation findings (no code or
recovery-behavior defect). All were fixed on the same branch within the original
14 authorized files; the amendment touched only these ten: `src/demo_run.py`,
`README.md`, `docs/demo-script.md`, `docs/hiring-manager-review.md`,
`docs/workflow-diagram.md`, `validation/uat-test-scripts.md`,
`validation/validation-summary.md`, `validation/risk-assessment.md`,
`validation/known-issues.md`, and this `AUTONOMOUS_STATUS.md`.

1. **UAT-015 now genuinely proves mid-operation rollback.** UAT-015 keeps the
   natural invalid-payload FAILED -> later-success case but no longer calls its
   zero writes "rollback after filing" (that payload fails validation before
   `_file_results` runs). A new subcase 15B mirrors
   `test_handled_mid_operation_failure_rolls_back_all_side_effects`: it injects an
   `InboundError` on the second `enter_fish_result` (after the first result and
   `RESULT_ENTERED` wrote) through the public `redrive_queue_item`, and proves the
   rollback removes all FISH rows / `RESULT_ENTERED` / `INBOUND_RESULT_FILED`,
   leaves the order and queue unchanged and OPEN, preserves the ERRORED message
   and FAILED attempt, shows `conn.in_transaction` false, then a new `request_id`
   succeeds. The fault injection is UAT setup only (no private helper, no manual
   queue update).
2. **Handled-failure language corrected** in `docs/demo-script.md`,
   `docs/hiring-manager-review.md`, `validation/validation-summary.md`, and
   `validation/risk-assessment.md`: a handled `InboundError` rolls back the filing
   side effects and queue resolution and then **commits** the approved
   handled-failure outcome (ERRORED message + FAILED attempt, queue OPEN); only an
   **unexpected** non-`InboundError` rolls back the whole request and re-raises.
3. **Recovery diagram corrected** in `docs/workflow-diagram.md`: SUCCEEDED,
   FAILED, and REJECTED each record an `interface_recovery_attempt` evidence node;
   `INBOUND_RESULT_FILED` is a separate audit event reached only from SUCCEEDED;
   `REQUEST_ID_CONFLICT` is audit-only. FAILED/REJECTED no longer imply a filing
   event.
4. **Remaining current-state facts corrected:** the hiring-manager scorecard now
   reads five scenarios and an approximately six-minute script; the README
   demo-script label is consistent; the branch-sensitive "Verified on `main`"
   wording is replaced with a current-tree verified-state statement that does not
   imply P3-004 is merged; `known-issues.md` persistence wording notes that the
   demo and most tests use in-memory SQLite while targeted recovery tests verify
   file-backed durability, with migration/pooling/concurrency limits remaining.
   Historical four-scenario and 61-test figures are preserved only where labeled
   historical.
5. **Demo evidence tightened** in `src/demo_run.py`: the immutability claim now
   compares `message_id`, `payload`, `control_id`, `status`, `created_at`, and the
   queue `raw_payload` before/after; the handled-FAILED demonstration reports
   accurately that the invalid payload produced no filing side effects (with
   read-only `RESULT_ENTERED` / `INBOUND_RESULT_FILED` counts) and does not claim
   it exercised mid-operation rollback.

No test, schema, query, sample, corpus, frozen file, interface mapping, public
signature, or recovery behavior was changed in this review response. After the
amendment: 164 pytest tests still pass across eight suites; `python -m
src.demo_run` exits 0 with five scenarios and every printed claim matching
persisted state; `git diff --check` clean; changed-line text is plain ASCII; all
relative Markdown links resolve; the full PR remains within the original 14
authorized files.

### Second review response - residual UAT-015B accuracy

A follow-up review of PR #21 raised one residual documentation issue in the
UAT-015B fault-injection subcase. Fixed in `validation/uat-test-scripts.md` (plus
this status record); no other file changed.

1. **Transaction wording corrected.** The subcase no longer says the first result
   "has committed within the operation" - it has only been *written* inside the
   still-open transaction. The setup and expected result now state accurately that
   the second-call fault occurs after the first result and its `RESULT_ENTERED`
   event have been written but **before** the transaction commits.
2. **`try/finally` restore.** The temporary `workflow.enter_fish_result`
   replacement and the `redrive_queue_item` call are now wrapped in `try/finally`
   so the real dependency is restored even if the call raises unexpectedly, with
   an explicit follow-up step confirming
   `workflow.enter_fish_result is real_enter`.
3. **Injection-point evidence.** `state["calls"] == 2` is now an inspected step
   and a captured evidence item, proving the injected failure occurred on the
   intended second call (so a real write preceded the fault).

The revised snippet was executed against a scratch in-memory database to confirm
it behaves as documented: `state["calls"] == 2`, dependency restored,
`outcome = FAILED`, zero `fish_result` / `RESULT_ENTERED` / `INBOUND_RESULT_FILED`,
order and queue rows unchanged and `OPEN`, attempted message `ERRORED`,
`conn.in_transaction` false, and a later new-`request_id` request `SUCCEEDED` with
the queue `RESOLVED`. No application code, test, schema, recovery behavior,
sample, frozen file, or other documentation was changed; 164 pytest tests still
pass, the demo still exits 0 with five scenarios, and the full PR remains within
the original 14 authorized files.

P3-004 passed independent final re-review and was accepted by Austin before PR
#21 merged into `main` as
`406509ad2847efdf5dc6a09f7f6de52e3dfb514b`. No P3-005 or other follow-on work
is approved, and the Autonomous Routine remains `DISABLED`.

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
| P3-003 - Controlled Recovery Service Core | Independent re-review passed; PR #19 merged into `main` | `672143ca4ae364d413ef38fdfdedf244fcc89f66` |
| P3-004 - Recovery Validation, UAT, and Portfolio Closeout | Independent final review passed; Austin accepted the closeout; PR #21 merged into `main` | `406509ad2847efdf5dc6a09f7f6de52e3dfb514b` |

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

## Accepted P3-003 scope

PR #19 passed independent re-review and merged into `main` as
`672143ca4ae364d413ef38fdfdedf244fcc89f66`. Austin explicitly approved
P3-003 and its bounded scope. The task implements the
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

## Accepted review response (PR #19)

Independent review found three blockers, all fixed before acceptance by changing
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

## Test evidence (accepted P3-003)

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

- Independently review and decide whether to merge this P3-005 authorization
  change.
- After acceptance, manually dispatch P3-005 once from the exact authorization
  merge commit.
- Decide separately whether the Autonomous Routine may ever be enabled. It
  remains `DISABLED` unless Austin explicitly authorizes that distinct change.

## Next permitted action

Open this governance-only authorization as a draft PR and stop for independent
review. No P3-005 execution may begin from the authorization branch.

If the authorization PR is accepted and merged, the next permitted action is
one manual dispatch of P3-005 from the exact merge commit under the task contract
above. The builder may create one evidence branch and one draft PR, then must
stop.

**Scheduled routines remain disabled.** P3-005 may begin only after this
authorization change is independently reviewed and merged and a human manually
dispatches it once. Hardening, new recovery behavior, UI/API/CLI, transport,
deployment, authentication, release work, and every other task remain
unapproved. Do not merge, deploy, release, enable auto-merge, or push to
`main`.
