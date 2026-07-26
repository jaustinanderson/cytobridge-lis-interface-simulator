# AGENTS.md - repository rules for all AI agents

These rules bind every AI agent working in this repository - Codex, Claude,
and any other builder or reviewer. `CLAUDE.md` requires Claude to read and
follow this file. It exists because of AF-2026-012 (see
[`AUDIT_FINDINGS.md`](AUDIT_FINDINGS.md)): the governance process once became
recursively self-maintaining, produced eleven pull requests without a product
change, and had to be stopped by a human.

## Progress and meta-analysis checkpoint (mandatory)

Before starting any new phase or opening any pull request, answer explicitly:

1. What user or product outcome changed, or will change, because of this work?
2. Is the next action substantive progress, or maintenance of prior
   governance?
3. Will this action require another pull request merely to correct its own
   status?
4. Has the complete execution-to-publication path been preflighted?
5. Is the evidence proportional to the claim?
6. Is reviewer independence being described truthfully?

If any answer fails, stop and escalate to Austin instead of proceeding.

## Publication preflight (before substantive execution)

Before any expensive or hard-to-repeat execution, verify end to end:
authenticated repository access, push capability, the exact expected baseline
commit, branch and pull-request collision state, and any required CI path.
A missing dependency or publication route is a pre-execution stop condition,
not something to discover after the work is done.

## Task, review, and status discipline

- One task, one branch, one pull request, one genuinely independent review.
- A review by the same agent that produced the work is a self-review; never
  describe it as independent. State plainly who authored and who reviewed.
- Never record transient pull-request state (open, draft, awaiting review,
  unmerged, current branch, head SHA) in version-controlled files. Durable
  files record durable facts; live state belongs in PR metadata.
- No status-only post-merge closeout PRs. A change must leave the tree
  accurate immediately after merge with no follow-up commit required.
- Evidence must be proportional to the claim: complete against the contract,
  and no larger than the claim requires. A report that dwarfs its deliverable
  is a defect, not diligence.

## Loop detection (mandatory pause)

Pause and escalate to Austin when any of these fires:

- two consecutive governance-only or status-only pull requests;
- two pull requests without a substantive product or final-deliverable
  change;
- a status record that would require a post-merge correction;
- the same evidence claim requiring a second amendment;
- review or administrative work exceeding the underlying deliverable;
- a human interrupts to identify a loop the agent failed to detect.

## Audit accountability

If Austin has to catch something the agent should have caught - a loop, a
stale claim, a missed preflight - that interruption automatically produces an
audit candidate in [`AUDIT_FINDINGS.md`](AUDIT_FINDINGS.md) and a process
correction. Record it; never absorb it silently.

## Standing project rules

All data is synthetic; never introduce PHI. Do not overstate standards
conformance, clinical validity, or production readiness. Never merge, deploy,
release, enable auto-merge, or push to `main`. Frozen files (listed in
`CLAUDE.md`) require Austin's explicit approval to change. The recurring
Autonomous Routine is `DISABLED`; new work requires a new bounded
authorization from Austin.
