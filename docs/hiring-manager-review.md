# Hiring-manager review

A review of the CytoBridge LIS Interface Simulator from the perspective of an
**LIS Analyst / Beaker-adjacent hiring manager** skimming the public repo for
10-15 minutes before deciding whether to talk to the candidate.

> **Synthetic learning project - no PHI.** This review judges the repo as a
> *portfolio artifact*, not as a clinical or production system. The project is
> **Beaker-adjacent learning, not Epic build experience** - a boundary the repo
> itself states repeatedly, which is exactly what a careful reviewer wants to
> see.

## Overall score: 8.9 / 10

> **Follow-up status (2026-07-11):** the review's two actionable engineering
> gaps are now closed. GitHub Actions runs the full suite and demo on Python 3.11
> and 3.12, and `tests/test_queries.py` gives every analyst SQL view a
> result-level assertion.

> **v1.1 follow-up assessment (2026-07):** the "what would you build next" item -
> a controlled error-queue resolve/re-drive workflow (former KI-03) - has since
> been designed, approved, implemented, and validated. The score is deliberately
> **held at 8.9**: the addition strengthens dimensions 6 (error queue /
> troubleshooting) and 7 (validation / UAT maturity), but breadth (one panel,
> educational interfaces, in-memory) is unchanged, so re-scoring is not warranted
> on that basis alone. Current evidence is **164 passing tests across eight
> suites**, **41/41 requirements** with passing automated coverage (manual UAT
> defined, not executed), and **five** clean demo scenarios. A notable maturity
> signal: the recovery behavior was specified in a **frozen, pre-implementation
> design record** and proven against two human-approved invariants. Provenance is
> stated plainly - the recovery safety design and decisions were the owner's,
> the implementation was done with an AI assistant under bounded instructions,
> and the owner reviewed and accepted the validation evidence; it is not
> presented as unaided work, certified HL7/FHIR, regulatory validation,
> production-ready software, or Epic build experience.

A standout portfolio project for a lab/interface analyst track. It reads like
someone who understands the *work* - order-to-result lifecycle, validation
gating, HL7/FHIR interface mapping in both directions, an error queue that
behaves, auditability, and a real validation/traceability package - and who is
scrupulously honest about what it is and is not. The main ceiling on the score
is breadth (one panel, educational interfaces, in-memory only), all of which is
disclosed rather than hidden.

## Scorecard

| # | Dimension | Score | One-line rationale |
|---|---|---|---|
| 1 | First impression / README clarity | 9 | Scoped, disclaimered, with layout, run steps, and a validation index; you know what it is in 30 seconds. |
| 2 | Clinical workflow relevance | 8 | Realistic AML/MDS FISH lifecycle with cutoff-aware interpretation; single panel + illustrative cutoffs (by design). |
| 3 | LIS / interface analyst relevance | 9 | Accessioning, result entry, validation gating, audit, analyst SQL worklists, and both interface directions - squarely the job. |
| 4 | SQL / data-model credibility | 9 | Real PK/FK/CHECK/UNIQUE constraints, enumerated statuses, indexes, upsert semantics; raw SQL, no ORM. |
| 5 | HL7 / FHIR-style interface credibility | 8 | Segment-accurate ORU (MSH/PID/OBR/SPM/OBX) + a proper FHIR Bundle, single-snapshot consistency, MSH-11=T; honestly "style, not certified". |
| 6 | Error queue / troubleshooting credibility | 9 | Every message stored, all-or-nothing filing, clear reasons, OPEN status, an analyst runbook that reads like real triage, and a controlled recovery service (immutable original, idempotent, transaction-safe). |
| 7 | Validation / UAT maturity | 9 | 41 traced requirements, a requirements-to-test matrix, 18 UAT scripts, risk assessment, known-issues, and a frozen pre-implementation design record with two human-approved invariants - unusually mature for a portfolio. |
| 8 | Portfolio honesty / no overclaiming | 10 | Repeated Epic/Beaker boundary, "what it does NOT prove", synthetic-data notices, training flags. Best-in-class. |
| 9 | Ease of demo | 9 | `python -m src.demo_run` (stdlib only), one dev dep, deterministic in-memory DB, 4 scenarios, a 5-minute script. |
| 10 | Resume / interview usefulness | 9 | Ready-made bullets, talking points, and a clear answer to "is this Epic experience?". |

## Strengths

- **Honesty is the headline.** The Epic/Beaker boundary, the "what it does NOT
  prove" section, `MSH-11 = T` (training), synthetic `urn:cytobridge:*` code
  systems, and "no PHI" notices appear everywhere. A reviewer's biggest fear
  with a "Beaker-adjacent" project - overclaiming - is pre-empted.
- **It behaves like a lab system, not a toy.** Finalization is *blocked* on any
  ERROR (including a cutoff-aware missed-abnormal), outbound export is
  finalized-only, and inbound filing is all-or-nothing so a bad message never
  half-updates an order. These are the exact integrity instincts the role wants.
- **The data model does real work.** Constraints live in the schema
  (`cells_abnormal <= cells_scored`, `UNIQUE (order_id, probe_id)`), not just in
  Python - so bad data can't exist, not merely get caught late.
- **The error queue is the differentiator.** Storing every inbound message and
  routing failures with a specific, human-readable reason (plus a runbook on how
  to resolve them) is precisely interface-analyst thinking.
- **Controlled recovery with real safety design.** The v1.1 service turns the
  error queue from a dead-letter box into a safe, idempotent workflow: the
  original message is immutable, only a new message can be filed, `request_id`
  makes replays no-ops and flags conflicts, every operation is transaction-safe,
  and terminal orders are rejected. It was built against a frozen design record
  and proven with two human-approved invariants - exactly the discipline you want
  before letting an analyst re-drive results.
- **A genuine validation package.** Requirements -> code -> test -> UAT
  traceability, a risk register, and a self-authored known-issues list signal
  someone who can work with QA and auditors.
- **Frictionless to evaluate.** Stdlib + SQLite, one dev dependency, 164 passing
  tests, a 5-scenario demo, and a timed demo script.

## Weaknesses

- **Breadth is narrow (by design).** One panel (AML/MDS FISH), one specimen
  type end-to-end, illustrative cutoffs. Disclosed, but a reviewer will note it.
- **Interfaces are educational, not engine-grade.** No MLLP framing, ACK/NACK,
  repetitions, or conformance validation; the FHIR/HL7 are "shaped like", not
  certified. Honestly stated, but it is not production interface-engine
  experience (Rhapsody/Cloverleaf/Mirth).
- **In-memory only.** No persistence story, migrations, concurrency, or auth;
  `actor` fields are free-text, not authenticated identities. (Recovery is
  verified file-backed for durability, but the demo remains in-memory.)
- **Recovery is educational, not engine-grade.** The v1.1 recovery service is
  synchronous, headless, and single-process - no scheduler, worker, async queue,
  UI, or API. It demonstrates the safety design, not a production recovery
  subsystem. Honestly stated in `known-issues.md`.
- **Resolved follow-ups:** KI-01 is closed by result-level tests for all six
  analyst queries, KI-03 is closed by the v1.1 controlled recovery service, and
  CI makes the test/demo evidence one-click verifiable.

## Red flags

**None material.** This is a clean, honest repo. The only real risk is *verbal*
overclaiming in an interview - saying "I have Beaker/Epic experience" instead of
"I built a synthetic simulator to learn the domain Beaker lives in." The repo's
own `portfolio-review.md` already scripts the correct, honest answer, which is
reassuring rather than concerning.

Minor items caught in this review (and fixed in this same PR) were stale
"lands in a later session" comments in `schema.sql` and a "deferred to a later
session" line in `interface-mapping.md` - both left over from before the
interfaces were built. Catching and fixing these is itself the kind of
doc-accuracy hygiene a reviewer likes to see.

## Recommended final polish changes

Prioritized; none are blockers.

1. **(Done in the original review PR)** Fix stale "later session" comments in `schema.sql` and
   the "deferred to a later session" line in `docs/interface-mapping.md`; remove
   the "PARTIAL partial" doubling in `validation-summary.md`.
2. **(Done)** Close KI-01 with `tests/test_queries.py`; R-019 is now PASS.
3. **(Done)** Improve roadmap readability with GitHub task-list checkboxes.
4. **(Done)** Add a one-line results summary and live CI badge near the top of
   the README for instant credibility on a skim.
5. **(Done)** Name the error-queue resolve/re-drive workflow (KI-03) as the next
   bounded enhancement and a concrete "what would you build next?" answer.

## Suggested 30-second explanation

"CytoBridge is a synthetic lab information system I built to learn the
order-to-result and interface work that a Beaker-style analyst does. It takes an
AML/MDS FISH order through specimen accessioning, per-probe results, validation,
and finalization, then generates outbound HL7 and FHIR-style messages and
ingests inbound instrument results - matching them to orders and routing bad
ones to an interface error queue with clear reasons, then recovering them safely
through a controlled retry / corrected-re-drive service. It is all synthetic
data, it is educational rather than a certified interface engine, and it is
Beaker-adjacent learning, not Epic experience."

## Suggested 2-minute interview walkthrough

1. **(0:00-0:20) Frame it honestly.** "Synthetic simulator, no PHI, educational
   HL7/FHIR - built to demonstrate LIS/interface analyst thinking, not Epic
   build experience."
2. **(0:20-0:50) The workflow and the gates.** Order -> accession -> per-probe
   results -> validation -> finalize. "Two integrity gates: validation blocks
   finalize on any error, including a cutoff-aware missed-abnormal; and outbound
   export only works on a finalized order."
3. **(0:50-1:20) Interfaces both ways.** Outbound HL7 ORU + FHIR
   DiagnosticReport from one snapshot so they agree; inbound ORU matched to an
   open order by accession. "Filing is all-or-nothing, so a partly-bad message
   never half-updates an order."
4. **(1:20-1:45) The error queue and recovery.** "Every inbound message is
   stored; failures go to an error queue with a specific reason. From there a
   controlled service recovers them safely - retry an unchanged message or
   re-drive a corrected one, with the original kept immutable, idempotency by
   request_id, and full rollback on failure. Nothing is silently dropped or
   half-updated."
5. **(1:45-2:00) Prove it.** "41 requirements each trace to a function or schema
   constraint, a test, and a UAT script; 164 tests pass across eight suites; the
   demo runs five scenarios end-to-end; and the recovery behavior was frozen in a
   design record before I wrote a line of it. I wrote the limitations down myself,
   and I'm clear that the recovery build was done with an AI assistant under my
   approved design, not unaided."

## Suggested resume bullet

> Built **CytoBridge**, a synthetic LIS + HL7/FHIR interface simulator (Python,
> SQLite, raw SQL) that models an AML/MDS FISH order end-to-end - accessioning,
> per-probe results, cutoff-aware validation, finalization, and audit - with
> outbound HL7/FHIR generation, inbound ORU ingestion routed through an interface
> error queue, and a controlled, idempotent error-queue recovery service
> (immutable original, transaction-safe rollback); 41 traceable requirements and
> 164 passing tests. (Synthetic data; educational, Beaker-adjacent - not Epic
> build experience.)

## Suggested LinkedIn project description

> **CytoBridge - LIS Interface Simulator** (personal project)
>
> A synthetic, analyst-first laboratory information system that models a
> cytogenetics/FISH workflow end-to-end: order and specimen accessioning,
> structured per-probe results, cutoff-aware validation that blocks
> finalization on errors, and a full audit trail. It generates outbound HL7
> ORU and FHIR DiagnosticReport-style messages from finalized orders and
> ingests inbound instrument results, matching them to open orders by
> accession and routing invalid or unmatched messages to an interface error
> queue with clear, actionable reasons - then recovers them through a
> controlled, idempotent retry / corrected-re-drive service that keeps the
> original message immutable and every operation transaction-safe.
>
> Built with Python and SQLite (raw SQL, no ORM) and backed by a real
> validation package - numbered requirements, a requirements-to-test
> traceability matrix, UAT scripts, a risk assessment, and 164 passing tests.
> The recovery behavior was specified in a frozen, pre-implementation design
> record and implemented with an AI assistant under bounded, owner-approved
> instructions.
>
> All data is synthetic (no PHI). This is educational, HL7/FHIR-*style* work
> and is **Beaker-adjacent learning, not Epic build experience** - it
> demonstrates the underlying LIS/interface analyst skills, not Epic software
> or build content.
