# Validation summary

A one-page summary of how the CytoBridge LIS Interface Simulator is validated,
what the current results are, and what "validated" does and does not mean here.

> **Synthetic, analyst-first learning project - no PHI, not a medical device.**
> This summary demonstrates a *validation mindset* (numbered requirements,
> traceability, automated + manual coverage, documented limitations). It is
> **not** a regulatory validation deliverable and confers no clinical fitness.
> This is **Beaker-adjacent learning, not Epic build experience**
> ([`docs/portfolio-review.md`](../docs/portfolio-review.md)).

## Object under validation

| Item | Value |
|---|---|
| System | CytoBridge LIS Interface Simulator (v1 + v1.1 controlled recovery) |
| Scope | AML/MDS FISH order -> result -> validation -> finalize -> outbound HL7/FHIR -> inbound ORU ingestion + error queue -> controlled error-queue recovery |
| Sessions/phases covered | v1: workflow/validation/audit, outbound HL7/FHIR, inbound + error queue. v1.1: recovery corpus, recovery schema, structured classification, controlled recovery service |
| Platform | Python (stdlib), SQLite (`sqlite3`), raw SQL, `pytest` |
| Data | 100% synthetic; no PHI |

## Validation approach

Validation is layered:

1. **Requirements** - 41 numbered, testable requirements: `R-001`-`R-019` (v1)
   in [`requirements.md`](requirements.md) and `R-020`-`R-041` (v1.1 controlled
   recovery) in the frozen [`v1.1-requirements.md`](v1.1-requirements.md).
2. **Automated tests** - `pytest` across **eight** suites (`test_workflow.py`,
   `test_validation.py`, `test_outbound_interfaces.py`,
   `test_inbound_interfaces.py`, `test_queries.py`, `test_recovery_schema.py`,
   `test_failure_classification.py`, `test_recovery_service.py`).
3. **Manual UAT** - 18 analyst-style acceptance scripts (`UAT-001`-`UAT-018`)
   in [`uat-test-scripts.md`](uat-test-scripts.md). These are **defined, not
   executed**.
4. **Traceability** - every requirement mapped to code (file/function or schema
   constraint), automated test, and UAT in the
   [traceability matrix](traceability-matrix.md).
5. **Executable demonstration** - `python -m src.demo_run` exercises the happy
   path, a blocked finalize, outbound export, inbound ingestion/error queue, and
   controlled recovery against a fresh in-memory database.

## Current results

| Metric | Result |
|---|---|
| Automated tests | **164 passed** (`pytest`, eight suites) |
| Requirements traced | 41 / 41 mapped to code + test + UAT |
| Requirements with automated coverage passing | 41 / 41 |
| Manual UAT | 18 / 18 **defined** (not executed; no manual pass claimed) |
| Demo scenarios | 5 / 5 run clean (`python -m src.demo_run`, exit 0) |
| Reproducibility | Deterministic - in-memory DB seeded from `schema.sql`; fixed sample messages and recovery corpus |

### Requirement-category coverage

| Category | Requirements | Automated | Manual UAT |
|---|---|---|---|
| Workflow (create, accession) | R-001, R-002 | PASS | DEFINED |
| Validation (probe, counts, cutoff, blocking) | R-003-R-006 | PASS | DEFINED |
| Audit (workflow + inbound) | R-007, R-016 | PASS | DEFINED |
| Outbound interfaces (HL7, FHIR, finalized-only) | R-008, R-009 | PASS | DEFINED |
| Inbound interfaces (store, file, error-queue routing) | R-010-R-015, R-017, R-018 | PASS | DEFINED |
| Analyst SQL views | R-019 | PASS | DEFINED |
| Recovery classification | R-020, R-021 | PASS | DEFINED |
| Recovery immutability/lineage | R-022-R-024 | PASS | DEFINED |
| Recovery retry/re-drive/terminal | R-025-R-029 | PASS | DEFINED |
| Recovery state model | R-030, R-031 | PASS | DEFINED |
| Recovery outcomes/history | R-032, R-033 | PASS | DEFINED |
| Recovery idempotency/atomicity | R-034-R-038, R-041 | PASS | DEFINED |
| Recovery service/audit | R-039, R-040 | PASS | DEFINED |

The **automated** column is a test that runs and passes; the **manual UAT**
column is `DEFINED` (a written procedure exists) and is not a claim that a tester
executed it. No document in this package asserts a manual UAT passed.

## v1.1 controlled-recovery evidence

The controlled recovery service (`src/recovery.py`) is proven by
`tests/test_recovery_service.py` (54 tests), `tests/test_recovery_schema.py` (29
tests), and `tests/test_failure_classification.py` (20 tests):

- **All twelve corrected re-drive fixtures** in the approved corpus succeed
  automatically (parameterized `test_corrected_redrive_succeeds_for_every_recoverable_case`).
  The demo shows representative cases; the automated suite is what proves all
  twelve.
- **Both human-approved invariants** pass without weakening: I-01
  (original-message immutability) and I-02 (duplicate/replay protection).
- **Outcome coverage:** `SUCCEEDED` (new FILED message + queue RESOLVED),
  `FAILED` (handled failure - attempted message preserved as ERRORED, all filing
  side effects rolled back, queue left OPEN), and `REJECTED` (prohibited action
  or closed item; no processing message).
- **Idempotency and conflict:** matching `request_id` **replay** returns the
  recorded attempt and writes nothing (proven for prior SUCCEEDED, FAILED, and
  REJECTED); a mismatched `request_id` reuse is a `REQUEST_ID_CONFLICT` that
  raises, fabricates nothing, and records exactly one audit event.
- **Terminalization:** a request against a TERMINAL item is rejected, and a
  permitted retry whose target order is now FINALIZED/CANCELLED moves the queue
  `OPEN -> TERMINAL` without filing or reopening the order.
- **History:** `get_recovery_history` returns every attempt in order and excludes
  conflict pseudo-attempts.
- **Rollback:** a handled mid-operation failure and an unexpected failure (before
  or after filing) both roll the whole request back, leaving no dangling
  transaction; generic/database errors are never converted to FAILED.
- **Durability and integrity:** file-backed durability holds after both a success
  and a handled failure, and `PRAGMA foreign_key_check` returns no violations
  across success, failure, rejection, replay, conflict, and terminalization.

The recovery design, requirements, and test intent are frozen pre-implementation
decision records ([`v1.1-design-record.md`](v1.1-design-record.md),
[`v1.1-requirements.md`](v1.1-requirements.md),
[`v1.1-test-intent.md`](v1.1-test-intent.md)); the implementation evidence above
is recorded here and in the traceability matrix, not in those frozen files.

## Analyst-query coverage

All six `queries/*.sql` views have deterministic result-level assertions.
`tests/test_queries.py` covers pending review, STAT aging, turnaround time,
validation error rate, and parameterized audit lookup;
`tests/test_inbound_interfaces.py` covers the open interface-error worklist.
This closes KI-01 and makes R-019 fully verified within the project's synthetic
scope.

Broader boundaries (educational HL7/FHIR only, single panel, no auth/security
model, in-memory demo DB) are enumerated in [`known-issues.md`](known-issues.md)
and assessed in [`risk-assessment.md`](risk-assessment.md).

## How to reproduce

```bash
pip install -r requirements-dev.txt   # pytest only
python -m pytest -q                   # -> 164 passed
python -m src.demo_run                 # -> 5 scenarios, exit 0
```

Then execute the manual layer with [`uat-test-scripts.md`](uat-test-scripts.md)
and record results in its summary table before claiming any manual pass.

## Conclusion

Within its stated synthetic scope, the simulator meets all 41 requirements, each
backed by result-level automated coverage that runs and passes today and a
defined (not executed) manual UAT. The workflow, validation, outbound generation,
inbound ingestion/error-queue, and controlled recovery behaviors are traceable,
reproducible, and demonstrated end-to-end. This remains an **educational,
synthetic, non-regulatory** portfolio artifact - not a validated medical device,
certified interface engine, or production LIS. Remaining gaps are documented,
bounded, and non-blocking for the project's purpose as a demonstration of
LIS/interface analyst thinking.
