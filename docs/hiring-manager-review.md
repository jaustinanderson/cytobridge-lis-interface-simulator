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
| 6 | Error queue / troubleshooting credibility | 9 | Every message stored, all-or-nothing filing, clear reasons, OPEN status, and an analyst runbook that reads like real triage. |
| 7 | Validation / UAT maturity | 9 | 19 traced requirements, a requirements-to-test matrix, 10 UAT scripts, risk assessment, known-issues - unusually mature for a portfolio. |
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
- **A genuine validation package.** Requirements -> code -> test -> UAT
  traceability, a risk register, and a self-authored known-issues list signal
  someone who can work with QA and auditors.
- **Frictionless to evaluate.** Stdlib + SQLite, one dev dependency, 56 passing
  tests, a 4-scenario demo, and a timed demo script.

## Weaknesses

- **Breadth is narrow (by design).** One panel (AML/MDS FISH), one specimen
  type end-to-end, illustrative cutoffs. Disclosed, but a reviewer will note it.
- **Interfaces are educational, not engine-grade.** No MLLP framing, ACK/NACK,
  repetitions, or conformance validation; the FHIR/HL7 are "shaped like", not
  certified. Honestly stated, but it is not production interface-engine
  experience (Rhapsody/Cloverleaf/Mirth).
- **In-memory only.** No persistence story, migrations, concurrency, or auth;
  `actor` fields are free-text, not authenticated identities.
- **One coverage gap (KI-01).** Five of six analyst queries are exercised for
  runnability but only one has a result-asserting test - already tracked, worth
  closing.
- **No automation gate.** Tests are run by hand (CI is intentionally out of
  scope), so "56 passing" is a claim a reviewer must reproduce locally (easy,
  but not one-click).

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

1. **(Done in this PR)** Fix stale "later session" comments in `schema.sql` and
   the "deferred to a later session" line in `docs/interface-mapping.md`; remove
   the "PARTIAL partial" doubling in `validation-summary.md`.
2. **Close KI-01.** Add a small `tests/test_queries.py` that asserts each
   `queries/*.sql` returns the expected shape against seeded synthetic data. This
   turns R-019 from PARTIAL to fully verified and removes the one honest gap.
3. **Roadmap readability (cosmetic).** The README "Done:" list renders as
   `- PASS <item>` (an artifact of the earlier ASCII pass). Consider GitHub
   task-list checkboxes (`- [x] <item>`) or dropping the `PASS ` prefix, since
   the "Done:" heading already conveys completion.
4. **Add a one-line results line near the top of the README** (e.g. "56 tests
   passing; 4-scenario demo; 19 traced requirements") for instant credibility on
   a skim - without adding a CI badge (CI is out of scope).
5. **Name the next feature.** The error-queue resolve/re-drive workflow (KI-03)
   is the obvious next increment and makes a great "what would you build next?"
   answer - consider a short stub section in the roadmap.

## Suggested 30-second explanation

"CytoBridge is a synthetic lab information system I built to learn the
order-to-result and interface work that a Beaker-style analyst does. It takes an
AML/MDS FISH order through specimen accessioning, per-probe results, validation,
and finalization, then generates outbound HL7 and FHIR-style messages and
ingests inbound instrument results - matching them to orders and routing bad
ones to an interface error queue with clear reasons. It is all synthetic data,
it is educational rather than a certified interface engine, and it is
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
4. **(1:20-1:45) The error queue.** "Every inbound message is stored; failures
   go to an error queue with a specific reason and an analyst runbook to resolve
   them - nothing is silently dropped."
5. **(1:45-2:00) Prove it.** "19 requirements each trace to a function, a test,
   and a UAT script; 56 tests pass; the demo runs four scenarios end-to-end. And
   I wrote the limitations down myself."

## Suggested resume bullet

> Built **CytoBridge**, a synthetic LIS + HL7/FHIR interface simulator (Python,
> SQLite, raw SQL) that models an AML/MDS FISH order end-to-end - accessioning,
> per-probe results, cutoff-aware validation, finalization, and audit - with
> outbound HL7/FHIR generation and inbound ORU ingestion routed through an
> interface error queue; 19 traceable requirements and 56 passing tests.
> (Synthetic data; educational, Beaker-adjacent - not Epic build experience.)

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
> queue with clear, actionable reasons.
>
> Built with Python and SQLite (raw SQL, no ORM) and backed by a real
> validation package - numbered requirements, a requirements-to-test
> traceability matrix, UAT scripts, a risk assessment, and 56 passing tests.
>
> All data is synthetic (no PHI). This is educational, HL7/FHIR-*style* work
> and is **Beaker-adjacent learning, not Epic build experience** - it
> demonstrates the underlying LIS/interface analyst skills, not Epic software
> or build content.
