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
- `CONTROLLED`: the preventive rule, check, template, or gate has been added
  **and independently accepted**.
- `CLOSED`: later independent evidence from an approved task or pilot
  demonstrates that the correction and the preventive control operated as
  intended.
- `ACCEPTED_RISK`: Austin explicitly accepts the remaining risk and rationale.

These three states are distinct and must not be collapsed:

| To reach | What is required | What is **not** required |
|---|---|---|
| `CORRECTED` | The immediate defect or inaccurate claim is fixed. | A preventive control. |
| `CONTROLLED` | The preventive rule/check/template/gate exists and has been independently accepted. | Pilot or later-task evidence. Acceptance of the control is enough. |
| `CLOSED` | Later independent evidence from an approved task or pilot shows the correction and the control operated as intended. | - |

A finding is not corrected into closure: fixing the sentence or the code reaches
`CORRECTED` only. Adding and having the preventive measure accepted reaches
`CONTROLLED`; it does **not** additionally require pilot evidence. Only `CLOSED`
requires later operating evidence from an approved task or pilot.

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
10. Owner, status, and **lifecycle evidence**.

**Lifecycle evidence** means the evidence appropriate to the finding's *current*
status - correction evidence, control evidence, closure evidence, or
accepted-risk evidence - not closure evidence in every case. A finding is
complete when it records the evidence its current status supports and states
explicitly what the next transition still requires.

Accordingly:

- A `CORRECTED` finding records **Correction evidence**, and states that
  **Control evidence** and **Closure evidence** are *pending* (that is, awaiting
  the next lifecycle step) rather than treating them as missing fields.
- A `CONTROLLED` finding adds **Control evidence** (the accepted preventive
  measure) and states that **Closure evidence** is pending.
- A `CLOSED` finding adds **Closure evidence** from the approved task or pilot.
- An `ACCEPTED_RISK` finding records **Accepted-risk evidence**: Austin's
  explicit acceptance and rationale.

A pending evidence line is a satisfied requirement, not an omission. The first
six findings below are `CONTROLLED`; each records Correction evidence and
Control evidence, and marks Closure evidence as pending with the specific
prerequisite named. AF-2026-007 is `CONFIRMED`; its proposed correction and
stronger closeout control have not yet been independently accepted or merged.

## Summary

| ID | Finding | Classification | Severity | Status |
|---|---|---|---|---|
| AF-2026-001 | UAT claimed rollback without reaching a filing write | Evidence validity | High | CONTROLLED |
| AF-2026-002 | Handled and unexpected rollback semantics were conflated | Semantic accuracy | High | CONTROLLED |
| AF-2026-003 | Recovery diagram associated filing audit evidence with non-success outcomes | Traceability/diagram | High | CONTROLLED |
| AF-2026-004 | Current-state counts and claim strength drifted | State accuracy | Medium | CONTROLLED |
| AF-2026-005 | UAT snippet confused written with committed and lacked guaranteed cleanup | Procedure robustness | Medium | CONTROLLED |
| AF-2026-006 | Findings were closed before prevention controls were verified | Governance status | Medium | CONTROLLED |
| AF-2026-007 | Post-merge closeout left branch-time status claims on main | State accuracy/governance | Medium | CONFIRMED |

**Current lifecycle position.** AF-2026-001 through AF-2026-006 are
`CONTROLLED`: every immediate defect or inaccurate claim has been fixed, and
each preventive control was independently accepted through PR #23. AF-2026-007
is `CONFIRMED`; the correction and stronger closeout control are proposed in the
current governance change but are not yet accepted on `main`. No finding is
`CLOSED`.

**Control acceptance evidence.** PR #23 passed independent final review at head
`4ad36e0e73781c32d7a399875b94888ee835541e`; Austin explicitly authorized its
merge, and the control entered `main` as
`553e43e209c13bc809c9be0e5f892129fdc4244a` on 2026-07-25. This activates the
protocol and supplies control evidence for AF-2026-001 through AF-2026-006. It
does not supply closure evidence.

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
- **Status:** CONTROLLED
- **Correction evidence:** Corrected UAT merged through PR #21; the final procedure
  records `state["calls"] == 2`, pre-commit writes, rolled-back evidence, and
  `conn.in_transaction is false`.
- **Control evidence:** PR #23 independently reviewed and accepted the
  fault-path UAT rule above; Austin authorized its merge, and the control entered
  `main` at `553e43e209c13bc809c9be0e5f892129fdc4244a`.
- **Closure evidence:** Pending - requires later evidence from an approved task or
  pilot that a fault-path UAT was written under that rule.

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
- **Status:** CONTROLLED
- **Correction evidence:** Corrected language merged through PR #21 and independently
  re-reviewed against the recovery service and tests.
- **Control evidence:** PR #23 independently reviewed and accepted the
  outcome-by-outcome persistence-matrix rule for transactional workflows; Austin
  authorized its merge, and the control entered `main` at
  `553e43e209c13bc809c9be0e5f892129fdc4244a`.
- **Closure evidence:** Pending - requires later evidence from an approved task or
  pilot that summary transaction language was reconciled against that matrix
  before approval.

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
- **Status:** CONTROLLED
- **Correction evidence:** Corrected diagram merged through PR #21 and independently
  checked against service behavior.
- **Control evidence:** PR #23 independently reviewed and accepted the rule that
  diagrams are testable claims whose every outcome edge is checked against
  persisted events; Austin authorized its merge, and the control entered `main`
  at `553e43e209c13bc809c9be0e5f892129fdc4244a`.
- **Closure evidence:** Pending - requires later evidence from an approved task or
  pilot that a validation diagram was reviewed edge-by-edge under that rule.

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
- **Status:** CONTROLLED
- **Correction evidence:** Corrections merged through PR #21; repository-wide stale
  checks, demo execution, link checks, ASCII checks, and independent review
  passed.
- **Control evidence:** PR #23 independently reviewed and accepted the closeout
  rule above; Austin authorized its merge, and the control entered `main` at
  `553e43e209c13bc809c9be0e5f892129fdc4244a`.
- **Closure evidence:** Pending - requires later evidence from an approved closeout
  task that a canonical fact sheet and stale-claim sweep were used.

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
- **Status:** CONTROLLED
- **Correction evidence:** Residual correction commit `4c5e08a` merged through PR
  #21; the revised snippet and full suite were independently re-run.
- **Control evidence:** PR #23 independently reviewed and accepted the rule that
  executable UAT snippets are code; Austin authorized its merge, and the control
  entered `main` at `553e43e209c13bc809c9be0e5f892129fdc4244a`.
- **Closure evidence:** Pending - requires later evidence from an approved task or
  pilot that a UAT snippet was authored and dry-run under that rule.

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
- **Immediate correction:** AF-2026-001 through AF-2026-005 were returned to
  CORRECTED pending independent acceptance. Their transitions to CONTROLLED and
  to CLOSED were deferred separately: CONTROLLED awaited acceptance of the
  preventive control, while CLOSED still awaits later operating evidence. This
  finding was recorded instead of silently fixing the summary.
- **Root cause:** The initial record treated documenting a prevention measure as
  equivalent to implementing and verifying it.
- **Future prevention/control:** Status transitions must cite evidence that every
  prerequisite in the lifecycle has occurred. A finding may not be closed in the
  same unreviewed change that first proposes its preventive control.
- **Owner:** Independent reviewer / audit record keeper
- **Status:** CONTROLLED
- **Correction evidence:** PR #23 amendment corrects all premature status and
  evidence labels, and reconciles the lifecycle definitions so that `CONTROLLED`
  requires independent acceptance of the preventive control only, while `CLOSED`
  requires later operating evidence from an approved task or pilot.
- **Control evidence:** PR #23 independently reviewed and accepted the rule that
  status transitions cite matching lifecycle evidence and that a finding is not
  closed in the same unreviewed change that first proposes its control; Austin
  authorized its merge, and the control entered `main` at
  `553e43e209c13bc809c9be0e5f892129fdc4244a`.
- **Closure evidence:** Pending - requires later evidence from an approved task or
  pilot that a finding transitioned only on cited, matching lifecycle evidence.

### AF-2026-007 - Post-merge closeout left branch-time status claims on main

- **Date:** 2026-07-25
- **Scope:** P3-004 post-merge closeout; README and change-control record after
  PR #21
- **Classification:** State accuracy/governance
- **Severity:** Medium
- **Diagnosis:** PR #21 merged the accepted P3-004 closeout into `main`, but the
  README still said the closeout was "under review" and the change-control
  summary and P3-004 detail still said it was a draft awaiting Austin's review
  and was not merged.
- **Evidence and impact:** `main` reached merge commit
  `406509ad2847efdf5dc6a09f7f6de52e3dfb514b`, and later governance work reached
  `de775d891bea997cf12d6e873e020978df1d6fc5`, while those two branch-time claims
  remained. A portfolio reviewer reading the public entry points could receive
  a state story that contradicted the repository history and live control
  record.
- **What should have happened:** The P3-004 acceptance closeout should have
  reconciled every branch-sensitive current-state claim, including the README
  and human-readable change-control record, or explicitly labeled any retained
  text as a historical snapshot.
- **Immediate correction:** This governance change replaces the README's
  under-review statement with the accepted PR #21 merge evidence and updates
  both P3-004 change-control locations to the accepted merge state.
- **Root cause:** The status-only closeout treated `AUTONOMOUS_STATUS.md` as the
  complete post-merge state surface. The earlier stale-claim sweep focused on
  the P3-004 branch diff and did not assign a post-merge owner or checklist for
  public and historical-summary documents whose branch-time wording would
  become stale only after merge.
- **Future prevention/control:** Every task-acceptance closeout must run a
  repository-wide branch-sensitive state sweep for the task ID and terms such as
  `under review`, `awaiting review`, `draft PR`, and `not merged`. It must
  reconcile at least the live control record, public README/current-state
  claims, and change-control summary/detail, and either update branch-time text
  or label it explicitly as historical. The closeout report must list the
  searched terms and affected files.
- **Owner:** Independent reviewer / audit record keeper
- **Status:** CONFIRMED
- **Confirmation evidence:** Exact stale claims were found on accepted `main` at
  `de775d891bea997cf12d6e873e020978df1d6fc5` and reconciled against the PR #21
  merge commit and Git history.
- **Correction evidence:** Pending - the correction exists only in the current
  unmerged governance change and requires independent review, Austin's merge
  authorization, and merge to `main`.
- **Control evidence:** Pending - the stronger post-merge sweep rule above must
  be independently accepted.
- **Closure evidence:** Pending - a later accepted task closeout must demonstrate
  the repository-wide branch-sensitive sweep and cite its results.
