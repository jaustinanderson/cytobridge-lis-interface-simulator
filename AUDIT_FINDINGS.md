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

## Progress and meta-analysis checkpoint (mandatory)

Before starting any new phase or opening any pull request, the agent must
answer these questions explicitly and honestly, and must stop if any answer
fails:

1. What user or product outcome changed, or will change, because of this work?
2. Is the next action substantive progress, or maintenance of prior
   governance?
3. Will this action require another pull request merely to correct its own
   status?
4. Has the complete execution-to-publication path (authentication, branch,
   push, PR creation, CI) been preflighted end to end?
5. Is the evidence proportional to the claim it supports?
6. Is reviewer independence being described truthfully - is the "independent"
   reviewer actually a different party from the author?

## Stop-and-escalate triggers (mandatory)

The agent must pause work and escalate to Austin - not continue, not
self-correct silently - when any of the following occurs:

- two consecutive governance-only or status-only pull requests;
- two pull requests in a row without a substantive product or
  final-deliverable change;
- a status record that would require a post-merge correction to stay
  accurate;
- the same evidence claim requiring a second amendment;
- review or administrative work exceeding the underlying deliverable in size
  or effort;
- a human having to interrupt and identify a loop the agent failed to detect.

A human interruption of that final kind automatically produces an audit
candidate and a process correction; it is never absorbed silently.

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

- A `CONFIRMED` finding records **Confirmation evidence**, and states that
  **Correction evidence**, **Control evidence**, and **Closure evidence** are
  *pending* until those later lifecycle prerequisites occur.
- A `CORRECTED` finding records **Correction evidence**, and states that
  **Control evidence** and **Closure evidence** are *pending* (that is, awaiting
  the next lifecycle step) rather than treating them as missing fields.
- A `CONTROLLED` finding adds **Control evidence** (the accepted preventive
  measure) and states that **Closure evidence** is pending.
- A `CLOSED` finding adds **Closure evidence** from the approved task or pilot.
- An `ACCEPTED_RISK` finding records **Accepted-risk evidence**: Austin's
  explicit acceptance and rationale.

A pending evidence line is a satisfied requirement, not an omission. Twelve
findings are recorded below. Each records the lifecycle evidence appropriate
to its current state and marks later evidence as pending.

## Summary

| ID | Finding | Classification | Severity | Status |
|---|---|---|---|---|
| AF-2026-001 | UAT claimed rollback without reaching a filing write | Evidence validity | High | CONTROLLED |
| AF-2026-002 | Handled and unexpected rollback semantics were conflated | Semantic accuracy | High | CONTROLLED |
| AF-2026-003 | Recovery diagram associated filing audit evidence with non-success outcomes | Traceability/diagram | High | CONTROLLED |
| AF-2026-004 | Current-state counts and claim strength drifted | State accuracy | Medium | CONTROLLED |
| AF-2026-005 | UAT snippet confused written with committed and lacked guaranteed cleanup | Procedure robustness | Medium | CONTROLLED |
| AF-2026-006 | Findings were closed before prevention controls were verified | Governance status | Medium | CONTROLLED |
| AF-2026-007 | Post-merge closeout left branch-time status claims on main | State accuracy/governance | Medium | CONTROLLED |
| AF-2026-008 | Authorization merge omitted a required post-merge state transition | State accuracy/governance | Medium | CONTROLLED |
| AF-2026-009 | Status contract required a commit to record its own final head | Evidence design/governance | Medium | CONTROLLED |
| AF-2026-010 | Publication prerequisites were not preflighted before UAT execution | Planning/dependency control | Medium | CONTROLLED (proposed) |
| AF-2026-011 | P3-005 evidence handoff omitted contract-required reproducibility evidence | Evidence completeness | High | CONTROLLED (proposed) |
| AF-2026-012 | The control plane became recursively self-maintaining and displaced project progress | Process design/governance | High | CONTROLLED (proposed) |

**Current lifecycle position.** AF-2026-001 through AF-2026-009 are
`CONTROLLED`: every immediate defect or inaccurate claim has been fixed, and
each preventive control has been independently accepted. AF-2026-001 through
AF-2026-006 were controlled through PR #23; AF-2026-007 was controlled through
PR #25; and AF-2026-008 plus AF-2026-009 were corrected and controlled through
amended PR #27. AF-2026-010 through AF-2026-012 carry corrections and controls
implemented by the v1.1 finalization consolidation; because that consolidation
is the same change that implements the controls, their `CONTROLLED` status is
proposed and takes effect only through genuinely independent acceptance of the
consolidation before merge. If that acceptance does not occur, all three
remain `CONFIRMED`. No finding is `CLOSED`.

**Control acceptance evidence.** PR #23 passed independent final review at head
`4ad36e0e73781c32d7a399875b94888ee835541e`; Austin explicitly authorized its
merge, and the control entered `main` as
`553e43e209c13bc809c9be0e5f892129fdc4244a` on 2026-07-25. This activates the
protocol and supplies control evidence for AF-2026-001 through AF-2026-006. It
does not supply closure evidence.

**AF-2026-007 control acceptance evidence.** PR #25 passed independent final
review at head `fc1c1b2846522362c5170cfe997bcf877b5f4aeb`; Austin explicitly
authorized its merge, and the correction plus stronger post-merge state-sweep
control entered `main` as
`8620feb24d83955d8ac3755e34a8e63d59ed8690` on 2026-07-25. This supplies
correction and control evidence for AF-2026-007; it does not supply closure
evidence.

**AF-2026-008 and AF-2026-009 control acceptance evidence.** Independent review
of P3-005 authorization PR #27 at original head
`a8c972d64f47e44dc634e8a6bb6a92f714c4fc9d` confirmed both defects. The
amended contract at final head
`7439a3da2d8fb90b94ea2ba0c6c265a42aaf9f87` separated authorization,
post-merge acceptance closeout, and manual dispatch; removed the
self-referential in-tree head requirement; and defined the corresponding
preventive rules. PR #27 passed independent final review, Austin explicitly
authorized its merge, and the corrected contract and controls entered `main`
as `beb62ed13e76525fc29545de23f51382e4e98412` on 2026-07-25. This supplies
correction and control evidence for AF-2026-008 and AF-2026-009; it does not
supply closure evidence.

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
- **Immediate correction:** PR #25 replaced the README's under-review statement
  with the accepted PR #21 merge evidence and updated both P3-004 change-control
  locations to the accepted merge state.
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
- **Status:** CONTROLLED
- **Confirmation evidence:** Exact stale claims were found on accepted `main` at
  `de775d891bea997cf12d6e873e020978df1d6fc5` and reconciled against the PR #21
  merge commit and Git history.
- **Correction evidence:** PR #25 passed independent final review at head
  `fc1c1b2846522362c5170cfe997bcf877b5f4aeb`; Austin authorized its merge, and
  the README and both P3-004 change-control locations entered `main` in their
  corrected state at `8620feb24d83955d8ac3755e34a8e63d59ed8690`.
- **Control evidence:** The stronger post-merge sweep rule above was independently
  reviewed and accepted through PR #25 and entered `main` at
  `8620feb24d83955d8ac3755e34a8e63d59ed8690`.
- **Closure evidence:** Pending - a later accepted task closeout must demonstrate
  the repository-wide branch-sensitive sweep and cite its results.

### AF-2026-008 - Authorization merge omitted a post-merge state transition

- **Date:** 2026-07-25
- **Scope:** P3-005 authorization PR #27; `AUTONOMOUS_STATUS.md` authorization,
  acceptance, and manual-dispatch gates
- **Classification:** State accuracy/governance
- **Severity:** Medium
- **Diagnosis:** The authorization draft said P3-005 would become approved and
  unblocked as soon as PR #27 merged, while the same committed status document
  would still describe the authorization as pending review and unmerged. The
  merge event therefore had no defined transition that reconciled live state
  before execution.
- **Evidence and impact:** At reviewed head
  `a8c972d64f47e44dc634e8a6bb6a92f714c4fc9d`, the current-state table,
  approved-task section, authorization state, execution gate, questions, and
  next-action text all made merge the direct unblocking event. If merged as
  written, `main` would carry contradictory state claims and could be used to
  dispatch P3-005 before a reviewer recorded the accepted authorization
  baseline. This would repeat the branch-sensitive failure controlled by
  AF-2026-007 at an authorization boundary.
- **What should have happened:** The plan should have separated Austin's task
  approval, merge of the authorization contract, a post-merge status-only
  acceptance closeout, and manual dispatch into distinct gates. Execution
  should remain fail-closed until the closeout records the exact authorization
  merge and reconciles every live-state surface.
- **Immediate correction:** Amend PR #27 so its merge accepts the task contract
  but does not unblock or dispatch P3-005. Require a separate status-only
  acceptance closeout, independent review, and closeout merge before a human
  may dispatch the pilot from the exact closeout baseline.
- **Root cause:** The authorization design tried to make one in-tree status
  record describe both its pre-merge review state and its post-merge accepted
  state, and conflated acceptance of the contract with operational unblocking.
- **Future prevention/control:** Whenever a merge changes task authorization or
  execution state, define an explicit post-merge reconciliation gate. Keep the
  task blocked until a status-only closeout records the merge evidence,
  reconciles branch-sensitive claims, passes independent review, and enters
  `main`.
- **Owner:** Independent reviewer / audit record keeper
- **Status:** CONTROLLED
- **Correction evidence:** PR #27 amended the task contract at final head
  `7439a3da2d8fb90b94ea2ba0c6c265a42aaf9f87` to separate contract
  authorization, the status-only acceptance closeout, and manual dispatch.
  After independent final review and Austin's explicit merge authorization, the
  correction entered `main` as
  `beb62ed13e76525fc29545de23f51382e4e98412`. This closeout records that
  accepted merge and reconciles the live authorization state before execution.
- **Control evidence:** The explicit post-merge reconciliation gate was
  independently reviewed and accepted through PR #27 and entered `main` at
  `beb62ed13e76525fc29545de23f51382e4e98412`. The gate keeps P3-005 blocked
  throughout this closeout and requires a separate human dispatch from the
  exact closeout merge commit.
- **Closure evidence:** Pending - requires a later approved task to demonstrate
  authorization merge, status closeout, and manual dispatch in the required
  order.

### AF-2026-009 - Status contract required a commit to record its own final head

- **Date:** 2026-07-25
- **Scope:** P3-005 authorization PR #27; `AUTONOMOUS_STATUS.md` evidence and
  draft-PR recording requirements
- **Classification:** Evidence design/governance
- **Severity:** Medium
- **Diagnosis:** The task contract required the committed status document to
  record the draft PR's exact final head SHA. Adding a commit SHA to a file
  changes that commit and produces a different head, so the requirement was
  self-referential and could not be satisfied truthfully.
- **Evidence and impact:** At reviewed head
  `a8c972d64f47e44dc634e8a6bb6a92f714c4fc9d`, the authorized status update
  included "draft PR/head" while completion also required the exact head SHA.
  Writing head H into `AUTONOMOUS_STATUS.md` would create head H-prime; writing
  H-prime would create another head. A builder could loop indefinitely or leave
  a stale SHA in the canonical status record, weakening the evidence chain.
- **What should have happened:** The in-tree status record should contain only
  stable identifiers available before its final commit: the execution baseline,
  task branch, and draft PR number. After every file commit is pushed, the PR
  description should record the exact final head SHA as external metadata.
- **Immediate correction:** Amend PR #27 to prohibit a self-referential final
  head in `AUTONOMOUS_STATUS.md`, require branch and PR number there, and require
  the PR description to be updated with the final head only after all file
  commits are pushed. Any later file commit requires the description to be
  refreshed again.
- **Root cause:** The evidence design did not distinguish facts stored inside a
  commit from metadata that can be known only after that commit exists.
- **Future prevention/control:** Split commit-addressable evidence into in-tree
  and out-of-tree layers. A committed file must never be required to identify
  its own final commit; final-head evidence belongs in PR metadata and is checked
  only after the last push.
- **Owner:** Independent reviewer / audit record keeper
- **Status:** CONTROLLED
- **Correction evidence:** PR #27 amended the recording contract at final head
  `7439a3da2d8fb90b94ea2ba0c6c265a42aaf9f87` so committed status records use
  only stable identifiers and final-head evidence remains in PR metadata. After
  independent final review and Austin's explicit merge authorization, the
  correction entered `main` as
  `beb62ed13e76525fc29545de23f51382e4e98412`.
- **Control evidence:** The in-tree/out-of-tree evidence-separation rule was
  independently reviewed and accepted through PR #27 and entered `main` at
  `beb62ed13e76525fc29545de23f51382e4e98412`. This closeout applies the same
  rule by recording the stable PR #27 merge while leaving its own eventual merge
  commit for the human dispatch record after that commit exists.
- **Closure evidence:** Pending - requires a later approved task to record branch
  and PR number in-tree and the exact final head in PR metadata after the last
  push.

### AF-2026-010 - Publication prerequisites were not preflighted before UAT execution

- **Date:** 2026-07-25
- **Scope:** P3-005 execution and publication; local evidence commit
  `9fd9fb8d5d1ff85c2a6113bb30ad5080f1781354`; draft PR #29
- **Classification:** Planning/dependency control
- **Severity:** Medium
- **Diagnosis:** The P3-005 execution gate verified the repository baseline,
  branch collision, test environment, and UAT prerequisites, but did not verify
  an authenticated GitHub publication path before the recorded UAT began. The
  validated local evidence commit therefore existed before the builder
  discovered that the workspace had neither GitHub CLI authentication nor Git
  push credentials.
- **Evidence and impact:** After all nine UAT entries passed and the local
  commit was created, direct publication stopped because `gh` was unavailable
  and the HTTPS remote had no usable credentials. Publication required a second
  explicit authorization and a connected-app fallback that reconstructed an
  equivalent Git tree (local and GitHub commits share tree
  `740c6bf572a8322558d48ff7f3c03d3a55eaf279`). The evidence was preserved, but
  the unplanned handoff increased delay, commit-chain complexity, and the risk
  of stranded or incorrectly substituted evidence.
- **What should have happened:** Before recorded execution, the task should
  have verified one authorized, authenticated publication route capable of
  creating the required branch and draft PR, or defined an explicitly
  authorized equivalent-tree fallback before execution.
- **Immediate correction:** The failed preflight is disclosed in the durable
  [execution report](validation/v1.1-recovery-uat-execution-report.md), and the
  v1.1 finalization itself performed a full authenticated publication preflight
  before any editing began.
- **Root cause:** Execution-environment readiness and remote-publication
  readiness were treated as separate phases; the plan assumed a validated local
  commit could be pushed without making publication authentication part of the
  pre-execution gate.
- **Future prevention/control:** Every repository task that must publish
  evidence must preflight, before irreversible or expensive execution: the
  accepted remote baseline, branch/PR collision state, an authenticated write
  route, and any approved fallback. A missing route is a pre-execution stop
  condition. This gate is mandatory in the progress checkpoint above, in
  [`AGENTS.md`](AGENTS.md), and in the pull-request template.
- **Owner:** Independent reviewer / audit record keeper
- **Status:** CONTROLLED (proposed - effective only on genuinely independent
  acceptance of the v1.1 finalization consolidation; otherwise CONFIRMED)
- **Confirmation evidence:** Independent review of the P3-005 evidence PR
  compared the task contract, publication history, and local/equivalent tree
  chain and confirmed that publication credentials were discovered missing only
  after UAT execution and local commit creation.
- **Correction evidence:** The consolidated execution report discloses the
  failure, and the finalization change demonstrated the preflight-first order.
- **Control evidence:** The publication-preflight gate is embedded in this
  protocol, `AGENTS.md`, and the pull-request template by the same
  consolidation; acceptance requires genuinely independent review.
- **Closure evidence:** Pending - requires a later approved task to demonstrate
  the accepted publication preflight before execution begins.

### AF-2026-011 - P3-005 evidence handoff was incomplete

- **Date:** 2026-07-25
- **Scope:** P3-005 execution report and draft PR #29 as first reviewed
- **Classification:** Evidence completeness
- **Severity:** High
- **Diagnosis:** The first published execution report recorded PASS outcomes
  and detailed observations, but omitted contract-required exact commands or
  snippets for several UATs, the actual read-only pre-fault capture code for
  UAT-015B, and a full UAT-017 `outcome_detail` example; the PR description
  omitted the required UAT result table. The report simultaneously said
  `No audit candidate identified` even though the publication-preflight gap was
  already known.
- **Evidence and impact:** The submitted evidence could not independently prove
  every required handoff element and was not merge-ready; independent review
  blocked acceptance. The subsequent amendment then grew the report to 1,019
  lines, trading one evidence failure for a proportionality failure (see
  AF-2026-012).
- **What should have happened:** Before discarding scratch evidence and opening
  the PR, the builder should have mapped every completion criterion to an exact
  report section, captured the actual executed snippets directly, and reported
  the known preflight failure as an audit candidate.
- **Immediate correction:** A clearly labeled independent replay in fresh
  synthetic state supplied the missing evidence without reconstructing the
  deleted runner. The durable consolidated
  [execution report](validation/v1.1-recovery-uat-execution-report.md)
  preserves the material evidence - including the UAT-015B in-transaction
  milestone and complete UAT-017 details - at proportional length and
  discloses both process failures.
- **Root cause:** The completion review emphasized PASS accuracy and repository
  scope but did not perform a line-by-line handoff-completeness check against
  every completion requirement before publication.
- **Future prevention/control:** Evidence must be complete against the contract
  *and* proportional to the claim: map each requirement to its exact evidence
  location before publication, and treat both a missing element and an
  evidence artifact that dwarfs its deliverable as publication stop conditions.
  This rule is embedded in the progress checkpoint above, `AGENTS.md`, and the
  pull-request template.
- **Owner:** Independent reviewer / audit record keeper
- **Status:** CONTROLLED (proposed - effective only on genuinely independent
  acceptance of the v1.1 finalization consolidation; otherwise CONFIRMED)
- **Confirmation evidence:** Independent review of the first P3-005 evidence
  handoff identified the omissions line by line against the completion contract
  and blocked merge.
- **Correction evidence:** The independent replay supplied the missing exact
  evidence, and the consolidated report preserves it durably at proportional
  length.
- **Control evidence:** The evidence-completeness-and-proportionality rule is
  embedded in this protocol, `AGENTS.md`, and the pull-request template by the
  same consolidation; acceptance requires genuinely independent review.
- **Closure evidence:** Pending - requires a later approved evidence task to
  demonstrate the accepted handoff control before publication.

### AF-2026-012 - The control plane became recursively self-maintaining and displaced project progress

- **Date:** 2026-07-26
- **Scope:** The v1.1 governance and status workflow from PR #20 through the
  drafted PR #29/#30 sequence
- **Classification:** Process design/governance
- **Severity:** High
- **Diagnosis:** The version-controlled control plane - status document, audit
  ledger, closeout contracts, and their correction PRs - became recursively
  self-maintaining. Each governance change recorded transient state that a
  later change had to correct, so governance work generated more governance
  work while the product stood still.
- **Evidence and impact:**
  - Austin - not the agent - stopped the workflow and requested this
    meta-evaluation. Without Austin's interruption, the agent would have
    continued the PR #29/#30 merge-and-closeout sequence.
  - PR #29 and PR #30 recorded their own draft/review state in version-
    controlled files, so merging them would necessarily have created
    additional stale-state correction work.
  - No product behavior changed after PR #19 (the recovery service core)
    despite eleven subsequent pull requests.
  - `AUTONOMOUS_STATUS.md` grew from roughly 55 lines to more than 800
    proposed lines.
  - The UAT execution report grew to 1,019 lines for nine required
    executions.
  - The same agent sometimes acted as builder and reviewer while calling the
    second pass independent.
  - The process optimized for procedural certainty rather than the project's
    outcome.
- **What should have happened:** Each phase should have asked what user or
  product outcome it changed, whether the next action was progress or
  self-maintenance, and whether its own status record would need a post-merge
  correction - and stopped when the answers failed.
- **Immediate correction:** The v1.1 finalization consolidation replaces the
  recursive PR #29/#30 sequence with one durable change: the status document
  is reduced to durable facts with no transient state, the execution evidence
  is consolidated at proportional length, v1.1 is declared complete, and no
  post-merge closeout or status-correction PR is required or permitted.
- **Root cause:** The process rewarded exhaustive procedural self-description.
  Status records tracked live publication state inside version control, which
  guaranteed staleness at merge; evidence and review effort had no
  proportionality bound; and no checkpoint asked whether the work advanced the
  project.
- **Future prevention/control:** The mandatory progress and meta-analysis
  checkpoint and stop-and-escalate triggers above; the repository-level agent
  rules in [`AGENTS.md`](AGENTS.md) (publication preflight, one task/one
  PR/one independent review, no transient state in version-controlled files,
  no status-only closeout PRs, honest review independence, proportional
  evidence); the pull-request template's "Progress and loop check" section;
  and the rule that a human having to interrupt an undetected loop is itself
  an audit finding with accountability recorded.
- **Owner:** Independent reviewer / audit record keeper
- **Status:** CONTROLLED (proposed - effective only on genuinely independent
  acceptance of the v1.1 finalization consolidation; otherwise CONFIRMED)
- **Confirmation evidence:** Austin's interruption and meta-evaluation request,
  the PR history after PR #19, and the growth of the status document and
  evidence report cited above.
- **Correction evidence:** The consolidation described under immediate
  correction, verified by the standard validation commands and a repository
  sweep showing no remaining transient current-state claims.
- **Control evidence:** The checkpoint, triggers, `AGENTS.md`, and template
  changes are implemented by the same consolidation; acceptance requires
  genuinely independent review.
- **Closure evidence:** Pending - requires later independent evidence that an
  approved task ran the checkpoint, respected the triggers, and completed
  without status-correction follow-up work.
