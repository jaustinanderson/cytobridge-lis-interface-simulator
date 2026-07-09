# Portfolio review

An honest self-assessment of the CytoBridge LIS Interface Simulator as a
portfolio artifact: what it proves, what it deliberately does **not** prove, the
Epic/Beaker boundary, and ready-to-use resume bullets and interview talking
points.

> **Synthetic learning project - no PHI.**

## What this project proves

Concrete, demonstrable skills a reviewer can verify by reading the repo and
running it:

- **Relational data modeling for a lab domain.** A normalized SQLite schema with
  real constraints - primary/foreign keys, enumerated statuses, `UNIQUE
  (order_id, probe_id)`, and a `CHECK (cells_abnormal <= cells_scored)` - that
  enforces integrity at the database, not just in application code.
- **A complete order-to-report workflow.** Patient -> order -> specimen ->
  accession -> per-probe results -> validation -> finalize, with correct status
  transitions and one-result-per-probe upsert semantics.
- **Rules-based validation with clinical-style nuance.** Typed findings
  (ERROR blocks finalize, WARNING is advisory) including a **cutoff-aware**
  interpretation-consistency check.
- **Interface thinking, both directions.** Outbound HL7 ORU^R01-style and FHIR
  DiagnosticReport-style generation from a single snapshot (so they agree), and
  inbound ORU-style ingestion with accession matching and **all-or-nothing**
  filing.
- **An interface error queue that behaves.** Every inbound message is stored;
  invalid/unmatched/non-fileable messages are routed to a queue with a clear,
  human-readable reason and a timestamp - nothing is silently dropped.
- **Auditability.** Every state change writes an `audit_event`; inbound filings
  are traceable back to the source message.
- **Analyst SQL.** Six worklist queries (pending review, STAT aging, turnaround,
  validation error rate, audit lookup, open interface errors).
- **Testing and traceability discipline.** 56 passing `pytest` tests plus a full
  requirements -> code -> test -> UAT [traceability matrix](../validation/traceability-matrix.md),
  a [validation summary](../validation/validation-summary.md), a
  [risk assessment](../validation/risk-assessment.md), and an honest
  [known-issues](../validation/known-issues.md) list.
- **Change control.** One session = one branch = one PR, documented in the
  [change-control log](../validation/change-control-log.md).

In short: it demonstrates how an **LIS / interface analyst** thinks - data
integrity, validation gating, interface mapping, error handling, auditability,
and disciplined documentation.

## What this project does NOT prove

Stated plainly so no reviewer is misled:

- **Not certified HL7/FHIR.** The messages are *style-only*, use synthetic local
  code systems, set HL7 `MSH-11 = T` (training), and are not conformance-tested.
  It does not prove production interface-engine configuration (e.g. Rhapsody,
  Cloverleaf, Mirth) or MLLP/ACK transport.
- **Not clinical validity.** Probe cutoffs are illustrative, not reference
  ranges; there is no real interpretive/clonal logic.
- **Not production engineering.** No auth/identity, no migrations, in-memory demo
  DB, no concurrency model, no UI, no deployment.
- **Not real data.** 100% synthetic; it proves nothing about PHI handling beyond
  the discipline of never using real data.
- **Not Epic build experience** - see the boundary below.

## The Epic / Beaker boundary (read this)

**This is Beaker-*adjacent* learning, not Epic build experience.**

- The project models the **general category** of system that Epic Beaker belongs
  to - a laboratory information system with orders, specimens, results,
  validation, and interfaces. "Beaker" is referenced only to name that category.
- It **does not** use Epic software, does not reproduce Epic build content,
  configuration, or screens, and contains **no proprietary Epic material**.
- It is **not** affiliated with, endorsed by, or connected to Epic Systems
  Corporation.
- Building this does **not** equate to Epic Beaker certification or hands-on Epic
  build/analyst experience. Represent it as what it is: a self-built, synthetic
  simulator that demonstrates the *underlying analyst skills* transferable to a
  Beaker-style role.

If asked in an interview whether this is Epic experience, the correct answer is:
"No - it's a project I built to learn the LIS/interface domain that Beaker lives
in. It shows the workflow and interface thinking; it's not Epic build work."

## Suggested resume bullets

Pick the ones that fit the role; keep the synthetic/educational framing.

- Built a synthetic **LIS + HL7/FHIR interface simulator** (Python, SQLite, raw
  SQL) modeling an AML/MDS FISH lab workflow end to end - order, specimen
  accessioning, per-probe results, validation, finalization, and audit trail.
- Implemented **rules-based result validation** with a cutoff-aware
  interpretation-consistency check that blocks finalization on missed-abnormal
  and missing-probe errors, with findings persisted for review.
- Generated **outbound HL7 ORU and FHIR DiagnosticReport-style** messages from
  finalized orders (finalized-only gating), and built **inbound ORU ingestion**
  that matches messages to open orders by accession and files per-probe results.
- Designed an **interface error queue** that stores every inbound message and
  routes invalid/unmatched/non-fileable messages with clear reasons - no silent
  drops - with an analyst SQL worklist.
- Authored a **validation package**: 19 traceable requirements, a
  requirements-to-test **traceability matrix**, 10 **UAT scripts**, risk
  assessment, and known-issues log; **56 passing automated tests**.

## Interview talking points

- **"Walk me through the data model."** Constraints do real work - the
  `cells_abnormal <= cells_scored` CHECK and `UNIQUE (order_id, probe_id)` are
  where I put integrity so bad data can't exist, not just get caught later.
- **"How do you prevent a bad result from going out?"** Two gates: validation
  blocks finalize on any ERROR (including a *missed abnormal* via the cutoff-aware
  check), and outbound generation is finalized-only, so an incomplete report
  can't be exported.
- **"What happens when a bad message comes in?"** It's stored, then routed to the
  error queue with a specific reason. Filing is all-or-nothing, so a partly-bad
  message never half-updates an order. I can show the exact test that proves it.
- **"How would you hand this to QA?"** Point at the traceability matrix - every
  requirement maps to the function, the test, and a manual UAT. And I wrote the
  limitations down myself.
- **"How is this related to Epic Beaker?"** It models the same category of
  system and the same analyst skills; it is explicitly not Epic software or build
  content. I'm careful not to overstate it.
- **"What would you do next?"** Close KI-01 (per-query tests) and build an
  error-queue resolve/re-drive workflow (KI-03) - both already documented.

## Bottom line

A focused, honest demonstration of laboratory and interface **analyst** thinking
- data integrity, validation, HL7/FHIR interface mapping in both directions,
error handling, auditability, and validation/traceability discipline - built on
synthetic data with its boundaries clearly drawn.
