# Audit findings ledger

This file is the durable record of mistakes, near misses, post hoc corrections,
and planning failures discovered while building CytoBridge. Its purpose is not to
assign blame. Its purpose is to convert each meaningful failure into a stronger
future control.

The independent reviewer (ChatGPT/Codex, acting as audit record keeper) owns the
decision to confirm and log findings. Claude and other builders must identify
possible findings during work and report them as `AUDIT CANDIDATE` items, but
they must not silently decide that a material problem is too small to record.

This ledger does not approve implementation work, change frozen product
semantics, or authorize a merge, deployment, release, or autonomous routine.

## When a finding is required

Log a finding when any of the following occurs:

- a mistake reaches a commit, pull request, demonstration, validation record, or
  user-facing claim;
- an independent review requires a substantive correction;
- a test or UAT procedure claims evidence it did not actually exercise;
- implementation, documentation, diagrams, and persisted behavior disagree;
- the task plan omitted a foreseeable dependency, decision, failure mode, or
  verification step;
- a workaround fixes the immediate symptom without addressing the cause;
- repeated rework reveals that the existing process control is insufficient;
- a near miss could have changed product behavior, evidence, governance, or
  safety if it had not been caught.

Ordinary development iteration is not automatically a finding. A failing test
found and corrected before any claim is made becomes a finding only when it
reveals a planning, requirements, evidence, or control weakness worth preventing.

## Required response during autonomous work

Before completing any approved task, the builder must report one of:

- `AUDIT CANDIDATE: <concise description>`, with the affected task, evidence,
  immediate containment, and whether Austin must decide anything; or
- `No audit candidate identified`.

A candidate does not become an official finding until the independent reviewer
confirms it. The reviewer is accountable for logging confirmed findings and for
telling Austin when a diagnosis, correction, or prevention measure is being
recorded.

## Finding lifecycle

- `CONFIRMED`: diagnosis is supported by evidence.
- `CORRECTED`: the immediate defect or inaccurate claim has been fixed.
- `CONTROLLED`: a preventive check, task rule, template, or gate has been added.
- `CLOSED`: the correction and preventive control have both been independently
  verified.
- `ACCEPTED_RISK`: Austin explicitly accepts the remaining risk and rationale.

A finding is not closed merely because the code or sentence was corrected.
Closure requires a prevention measure and evidence that the measure is in place.

## Required fields

Every confirmed finding must record:

1. Finding ID and date.
2. Task, pull request, or affected scope.
3. Classification and severity.
4. Diagnosis: what was wrong.
5. Evidence and impact.
6. What should have happened.
7. Immediate correction.
8. Root cause or contributing process weakness.
9. Future prevention/control.
10. Owner, status, and closure evidence.

## Summary

| ID | Finding | Classification | Severity | Status |
|---|---|---|---|---|
| AF-2026-001 | UAT claimed rollback without reaching a filing write | Evidence validity | High | CORRECTED |
| AF-2026-002 | Handled and unexpected rollback semantics were conflated | Semantic accuracy | High | CORRECTED |
| AF-2026-003 | Recovery diagram associated filing audit evidence with non-success outcomes | Traceability/diagram | High | CORRECTED |
| AF-2026-004 | Current-state counts and claim strength drifted | State accuracy | Medium | CORRECTED |
| AF-2026-005 | UAT snippet confused written with committed and lacked guaranteed cleanup | Procedure robustness | Medium | CORRECTED |
| AF-2026-006 | Findings were closed before prevention controls were verified | Governance status | Medium | CORRECTED |

## Findings

### AF-2026-001 - Rollback evidence did not reach the claimed failure point

- **Date:** 2026-07-24
- **Scope:** P3-004, PR #21, UAT-015
- **Classification:** Evidence validity
- **Severity:** High
- **Diagnosis:** The original UAT used an invalid payload that failed validation
  before `_file_results` ran, then described the absence of filed results as
  rollback after filing. Zero side effects did not prove that a partial write had
  occurred and been rolled back.
- **Evidence and impact:** Instrumentation showed zero `_file_results` calls and
  zero FISH writes. The product implementation was correct, but the manual
  validation claim overstated what the procedure proved.
- **What should have happened:** The UAT plan should have identified the exact
  pre-fault milestone required by the rollback claim and captured evidence that
  the milestone was reached.
- **Immediate correction:** UAT-015 retained the natural validation-failure case
  with accurate wording. UAT-015B added deterministic fault injection on the
  second `enter_fish_result` call through the public recovery service and proved
  the first write occurred before the fault and all filing side effects rolled
  back.
- **Root cause:** Acceptance criteria focused on the final empty state without
  proving path reachability or the intermediate state needed to support the
  claim.
- **Future prevention/control:** Any rollback, retry, recovery, or fault-path UAT
  must name the injection point, prove the pre-fault milestone was reached, and
  verify both persisted state and transaction state afterward.
- **Owner:** Independent reviewer / audit record keeper
- **Status:** CORRECTED
- **Correction evidence:** Corrected UAT merged through PR #21; the final procedure
  records `state["calls"] == 2`, pre-commit writes, rolled-back evidence, and
  `conn.in_transaction is false`.

### AF-2026-002 - Transaction outcomes were described too broadly

- **Date:** 2026-07-24
- **Scope:** P3-004 validation, risk, demo, and hiring-manager documentation
- **Classification:** Semantic accuracy
- **Severity:** High
- **Diagnosis:** Several documents described a handled `InboundError` as a
  "whole request" or "full" rollback. The approved behavior rolls back filing
  side effects and queue resolution but commits the ERRORED attempted message
  and FAILED recovery attempt. Only an unexpected non-`InboundError` rolls back
  the whole request and re-raises.
- **Evidence and impact:** The wording contradicted persisted behavior and blurred
  an important audit distinction. A reviewer could incorrectly conclude that no
  evidence of a handled failed attempt is retained.
- **What should have happened:** Documentation should have been written from an
  explicit outcome-by-outcome persistence matrix before using shorthand such as
  "full rollback."
- **Immediate correction:** All affected documents now distinguish handled
  failure from unexpected exception behavior and state which records persist.
- **Root cause:** A convenient transaction label replaced a precise description
  of the approved persistence boundary.
- **Future prevention/control:** For transactional workflows, every acceptance
  task must reconcile each outcome against a matrix of records created,
  preserved, rolled back, and re-raised before approving summary language.
- **Owner:** Independent reviewer / audit record keeper
- **Status:** CORRECTED
- **Correction evidence:** Corrected language merged through PR #21 and independently
  re-reviewed against the recovery service and tests.

### AF-2026-003 - Diagram implied false filing audit evidence

- **Date:** 2026-07-24
- **Scope:** P3-004, `docs/workflow-diagram.md`
- **Classification:** Traceability/diagram
- **Severity:** High
- **Diagnosis:** FAILED and REJECTED outcomes flowed into a node labeled with
  `INBOUND_RESULT_FILED`, even though only SUCCEEDED creates that audit event.
- **Evidence and impact:** The topology visually asserted evidence that the
  database does not create, weakening trust in the validation package.
- **What should have happened:** Each diagram edge should have been checked
  against the outcome-to-record matrix and the traceability evidence.
- **Immediate correction:** Recovery attempts are now shown for SUCCEEDED,
  FAILED, and REJECTED, while `INBOUND_RESULT_FILED` is a separate event
  reachable only from SUCCEEDED. `REQUEST_ID_CONFLICT` remains audit-only.
- **Root cause:** Multiple evidence types were compressed into one diagram node
  without preserving outcome-specific conditions.
- **Future prevention/control:** Treat diagrams as testable claims. Review every
  outcome edge against persisted events before calling a validation diagram
  complete.
- **Owner:** Independent reviewer / audit record keeper
- **Status:** CORRECTED
- **Correction evidence:** Corrected diagram merged through PR #21 and independently
  checked against service behavior.

### AF-2026-004 - Current-state claims drifted during a large closeout

- **Date:** 2026-07-24
- **Scope:** P3-004 README, hiring-manager review, known issues, and demo evidence
- **Classification:** State accuracy
- **Severity:** Medium
- **Diagnosis:** The closeout retained stale four-scenario and five-minute
  references, implied work was verified on `main` before merge, overstated
  in-memory-only testing, and claimed an original message was unchanged after
  comparing only a subset of relevant fields.
- **Evidence and impact:** Individually small statements created an inconsistent
  current-state story and made evidence stronger than the comparison supported.
- **What should have happened:** The task should have used one current-state
  inventory for counts, branch status, persistence coverage, and immutability
  fields, followed by a repository-wide stale-claim search.
- **Immediate correction:** Counts and timing were reconciled, branch-sensitive
  wording was corrected, file-backed durability tests were acknowledged, and the
  demo immutability comparison was expanded to all claimed fields plus queue raw
  payload.
- **Root cause:** A wide documentation update lacked a single canonical fact
  sheet and claim-to-evidence checklist.
- **Future prevention/control:** Closeout tasks must define canonical figures
  before editing, search for superseded values afterward, distinguish historical
  figures explicitly, and compare every field named by an immutability claim.
- **Owner:** Independent reviewer / audit record keeper
- **Status:** CORRECTED
- **Correction evidence:** Corrections merged through PR #21; repository-wide stale
  checks, demo execution, link checks, ASCII checks, and independent review
  passed.

### AF-2026-005 - UAT procedure wording and cleanup were not robust

- **Date:** 2026-07-24
- **Scope:** P3-004 second review response, UAT-015B
- **Classification:** Procedure robustness
- **Severity:** Medium
- **Diagnosis:** The first amendment said a result had "committed within the
  operation" even though it was only written inside the still-open transaction.
  The temporary dependency replacement also lacked `try/finally`, and the
  procedure did not explicitly capture the call count proving the fault occurred
  on the intended second call.
- **Evidence and impact:** If the write had truly committed, rollback could not
  remove it. An unexpected exception could also leave the dependency patched and
  contaminate later UAT steps.
- **What should have happened:** Executable validation snippets should use exact
  transaction vocabulary, restore all temporary instrumentation unconditionally,
  and record evidence of the intended injection point.
- **Immediate correction:** The wording now says "written before commit"; the
  replacement is restored in `finally`; dependency restoration and
  `state["calls"] == 2` are explicit evidence.
- **Root cause:** The validation snippet was reviewed mainly for its happy
  execution result, not as reusable test code with cleanup obligations and exact
  transaction semantics.
- **Future prevention/control:** Treat executable UAT snippets as code: require
  deterministic setup, unconditional teardown, explicit injection-point
  evidence, exact vocabulary, and an independent dry run.
- **Owner:** Independent reviewer / audit record keeper
- **Status:** CORRECTED
- **Correction evidence:** Residual correction commit `4c5e08a` merged through PR
  #21; the revised snippet and full suite were independently re-run.

### AF-2026-006 - Finding closure was claimed before control verification

- **Date:** 2026-07-24
- **Scope:** PR #23, initial audit-findings governance draft
- **Classification:** Governance status
- **Severity:** Medium
- **Diagnosis:** The first draft marked AF-2026-001 through AF-2026-005 CLOSED
  because their immediate defects had been corrected in PR #21 and prevention
  measures were described in this ledger. The new controls had not yet been
  accepted on `main` or exercised in a later autonomous task, so closure was
  premature under the ledger's own lifecycle.
- **Evidence and impact:** PR #23 itself was the first location where the controls
  existed. No independent review or pilot evidence yet demonstrated that future
  task specifications and builders would follow them.
- **What should have happened:** Historical corrections should have entered the
  ledger as CORRECTED. CONTROLLED should require acceptance of the governance
  control, and CLOSED should require later independent evidence that the control
  operated as intended.
- **Immediate correction:** AF-2026-001 through AF-2026-005 now remain CORRECTED.
  Their transition to CONTROLLED or CLOSED is explicitly deferred. This finding
  is recorded instead of silently fixing the summary.
- **Root cause:** The initial record treated documenting a prevention measure as
  equivalent to implementing and verifying it.
- **Future prevention/control:** Status transitions must cite evidence that every
  prerequisite in the lifecycle has occurred. A finding may not be closed in the
  same unreviewed change that first proposes its preventive control.
- **Owner:** Independent reviewer / audit record keeper
- **Status:** CORRECTED
- **Correction evidence:** PR #23 amendment corrects all premature status and
  evidence labels. CONTROLLED/CLOSED transitions remain pending independent
  acceptance and pilot evidence.
