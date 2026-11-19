# Portfolio review

Implementation was done with AI assistants; recovery followed my approved design and bounded instructions.

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
  accession -> per-probe results -> validation -> finalize, with validation gates (KI-05 still affects direct finalization calls; the command line refuses already-finalized or cancelled orders) and one-result-per-probe upsert semantics.
- **Rules-based validation with clinical-style nuance.** Typed findings
  (ERROR blocks finalize, WARNING is advisory) including a **cutoff-aware**
  interpretation-consistency check.
- **Interface thinking, both directions.** Outbound HL7 ORU^R01-style and FHIR
  DiagnosticReport-style generation from a single snapshot (so they agree), and
  inbound ORU-style ingestion with accession matching and filing subject to KI-04.
- **An interface error queue that behaves.** Every inbound message is stored;
  recognized inbound validation failures are routed to a queue with a clear,
  human-readable reason and a timestamp. KI-04 can leave partial results, a RECEIVED message and no queue item; reconciliation detects that condition.
- **Controlled error-queue recovery (v1.1).** A headless recovery service
  (`src/recovery.py`) lets an analyst retry an unchanged message or re-drive a
  corrected one, with real safety guarantees: the original failed message is
  **immutable** and only a new message can be `FILED`; recovery is **idempotent**
  (`request_id` replay is a no-op, mismatched reuse is a `REQUEST_ID_CONFLICT`,
  at most one success per item); every operation is **transaction-safe** (a
  handled failure rolls back all filing side effects and leaves the queue OPEN);
  and finalized/cancelled orders are **terminal** and rejected. Every attempt is
  auditable via `payload_sha256` and `outcome_detail`.
- **Auditability.** Inbound filings and recovery operations have audit evidence linked to their messages; not every database state change creates an audit event.
- **Analyst SQL.** Seven worklist queries (pending review, STAT aging, turnaround,
  validation error rate, audit lookup, open interface errors, analyst queue worklist).
- **Testing and traceability discipline.** 691 passing `pytest` tests and one platform skip (692 cases across 18 test modules, November 19) plus a requirements -> code -> test
  [traceability matrix](../validation/traceability-matrix.md) covering all 52 requirements (R-001 through R-042 also map to UAT; the ten lab requirements have no manual UAT), a [validation summary](../validation/validation-summary.md), a
  [risk assessment](../validation/risk-assessment.md), and an honest
  [known-issues](../validation/known-issues.md) list. The v1.1 recovery behavior
  was specified against a **frozen, pre-implementation design record** and proven
  by dedicated suites, including two human-approved invariants.
- **Change control.** One task = one branch = one PR, documented in the
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
- **Not a production recovery subsystem.** The v1.1 recovery service is
  synchronous, headless, and single-process - no scheduler, worker, async queue,
  UI, or API. It demonstrates the *safety design* of a recovery workflow, not a
  production interface-engine feature.
- **Not unaided authorship.** See the provenance note below.
- **Not Epic build experience** - see the boundary below.

## Provenance (v1.1 recovery)

Recovery behavior and safety decisions were **designed and approved by Austin**. This project
must never be represented as unaided work or as autonomous output its author
cannot explain. The frozen
[design record](../validation/v1.1-design-record.md),
[requirements](../validation/v1.1-requirements.md), and
[test intent](../validation/v1.1-test-intent.md) are the pre-implementation
decision records that made this possible.

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
  routes recognized validation failures with clear reasons - subject to KI-04 - with an analyst SQL worklist. KI-04 can leave partial results, a RECEIVED message and no queue item; reconciliation detects that condition.
- Implemented a **controlled error-queue recovery service** (retry / corrected
  re-drive / attempt history) with original-message immutability, `request_id`
  idempotency and conflict handling, transaction-safe rollback, and terminal
  rejection - specified against a frozen pre-implementation design record and
  proven by dedicated test suites (recovery behavior designed and approved by
  the project owner).
- Authored a **validation package**: 52 traceable requirements, a
  requirements-to-test **traceability matrix**, 19 **UAT scripts**, risk
  assessment, and known-issues log; **691 passing automated tests and one platform skip** (692 cases across 18 test modules, November 19).

## Interview talking points

- **"Walk me through the data model."** Constraints do real work - the
  `cells_abnormal <= cells_scored` CHECK and `UNIQUE (order_id, probe_id)` are
  where I put integrity so those invalid cell counts or duplicate order/probe results can't exist, not just get caught later.
- **"How do you prevent a bad result from going out?"** Two gates: validation
  blocks finalize on any ERROR (including a *missed abnormal* via the cutoff-aware
  check), and outbound generation is finalized-only, so an incomplete report
  can't be exported. KI-05 still affects direct finalization calls; the command line refuses already-finalized or cancelled orders.
- **"What happens when a bad message comes in?"** It's stored; recognized validation failures reach the error queue with a specific reason. This non-numeric sample is rejected before filing. KI-04 can leave partial results, a RECEIVED message and no queue item; reconciliation detects that condition. I can show the exact test that proves rejection for the non-numeric sample.
- **"How do you recover a failed message safely?"** Through a controlled service,
  not a manual SQL update. The original message is immutable, only a new message
  can be filed, `request_id` makes it idempotent (replay is a no-op, a mismatched
  reuse is a logged conflict), and every attempt commits or rolls back as a unit -
  a handled failure leaves the queue OPEN with the attempt recorded as FAILED. I
  can show the two human-approved invariant tests that prove immutability and
  replay protection.
- **"How would you hand this to QA?"** Point at the traceability matrix - all 52 requirements map to the function or schema constraint and the test; R-001 through R-042 also map to a manual UAT (the ten lab requirements have none). I keep automated and manual status separate and honest - the v1.1
  recovery UATs were executed by Codex and passed with recorded evidence and a same-agent replay, the v1 UATs and UAT-019 are defined but not manually executed - and I wrote the limitations down myself.
- **"How is this related to Epic Beaker?"** It models the same category of
  system and the same analyst skills; it is explicitly not Epic software or build
  content. I'm careful not to overstate it.
- **"Did you build the recovery logic yourself?"** The safety design and
  decisions are mine (the project owner's), captured in a frozen design record
  before any code. I don't
  represent it as unaided work.
- **"What would you do next?"** Nothing beyond the approved scope without
  sign-off. The error-queue resolve/re-drive workflow (former KI-03) is now built
  and reviewed; KI-01 is already closed with result-level tests for every analyst
  SQL view.

## Bottom line

A focused, honest demonstration of laboratory and interface **analyst** thinking
- data integrity, validation, HL7/FHIR interface mapping in both directions,
error handling, controlled and idempotent error-queue recovery, auditability, and
validation/traceability discipline - built on synthetic data with its boundaries
clearly drawn, and with the recovery work traceable to a human-approved design
record rather than presented as unaided output.
