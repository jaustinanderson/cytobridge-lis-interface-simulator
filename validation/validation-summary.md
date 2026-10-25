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
| System | CytoBridge LIS Interface Simulator (v1 + v1.1 controlled recovery + analyst investigation and recovery lab) |
| Scope | AML/MDS FISH order -> result -> validation -> finalize -> outbound HL7/FHIR -> inbound ORU ingestion + error queue -> controlled error-queue recovery, plus the headless analyst lab over those services |
| Sessions/phases covered | v1: workflow/validation/audit, outbound HL7/FHIR, inbound + error queue. v1.1: recovery corpus, recovery schema, structured classification, controlled recovery service. Issue #43: analyst CLI, case trace, reconciliation, recovery preview, exercises, case evidence |
| Platform | Python (stdlib), SQLite (`sqlite3`), raw SQL, `pytest` |
| Data | 100% synthetic; no PHI |

## Validation approach

Validation is layered:

1. **Requirements** - 51 numbered, testable requirements: `R-001`-`R-019` (v1)
   and `R-043`-`R-052` (analyst lab) in [`requirements.md`](requirements.md),
   and `R-020`-`R-041` (v1.1 controlled recovery) in the frozen
   [`v1.1-requirements.md`](v1.1-requirements.md).
2. **Automated tests** - `pytest` across **sixteen** suites (`test_workflow.py`,
   `test_validation.py`, `test_outbound_interfaces.py`,
   `test_inbound_interfaces.py`, `test_queries.py`, `test_recovery_schema.py`,
   `test_failure_classification.py`, `test_recovery_service.py`, and the eight
   `test_analyst_*.py` suites).
3. **Manual UAT** - 18 analyst-style acceptance scripts (`UAT-001`-`UAT-018`)
   in [`uat-test-scripts.md`](uat-test-scripts.md). UAT-011-UAT-018 (including
   both UAT-015 subcases) were **manually executed and passed** on 2026-07-25,
   with recorded evidence in the
   [recovery UAT execution report](v1.1-recovery-uat-execution-report.md);
   UAT-001-UAT-010 are **defined, not manually executed**. UAT-004 has separate
   [AI functional replay evidence](uat004-ai-functional-replay.json), which
   does not count as a human acceptance pass.
4. **Traceability** - every requirement mapped to code (file/function or schema
   constraint), automated test, and UAT in the
   [traceability matrix](traceability-matrix.md).
5. **Executable demonstration** - `python -m src.demo_run` exercises the happy
   path, a blocked finalize, outbound export, inbound ingestion/error queue, and
   controlled recovery against a fresh in-memory database.
6. **Executable incident exercises** - `python -m src.analyst exercise run-all
   --workdir DIR` runs seven synthetic incidents on new file databases through the analyst
   CLI. A verifier checks the persisted rows and fixture hashes against
   separate answer keys. This is automated evidence, not manual UAT, and it is
   not evidence of Austin's mastery.

## Current results

| Metric | Result |
|---|---|
| Automated tests | **600 passed** (`pytest`, sixteen suites) |
| Requirements traced | 51 / 51 mapped to code + test; the 41 v1/v1.1 requirements also map to a UAT |
| Requirements with automated coverage passing | 51 / 51 |
| Manual UAT | 18 / 18 defined; UAT-011-UAT-018 **executed and passed** (2026-07-25, [evidence](v1.1-recovery-uat-execution-report.md)); UAT-001-UAT-010 not manually executed |
| AI functional replay | UAT-004 PASS (2026-09-04, [synthetic evidence](uat004-ai-functional-replay.json)); human acceptance and Austin mastery remain pending |
| Demo scenarios | 5 / 5 run clean (`python -m src.demo_run`, exit 0) |
| Analyst lab exercises | 7 / 7 verified from persisted records (`python -m src.analyst exercise run-all --workdir DIR`, automated) |
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
| Recovery classification | R-020, R-021 | PASS | PASS |
| Recovery immutability/lineage | R-022-R-024 | PASS | PASS |
| Recovery retry/re-drive/terminal | R-025-R-029 | PASS | PASS |
| Recovery state model | R-030, R-031 | PASS | PASS |
| Recovery outcomes/history | R-032, R-033 | PASS | PASS |
| Recovery idempotency/atomicity | R-034-R-038, R-041 | PASS | PASS |
| Recovery service/audit | R-039, R-040 | PASS | PASS |
| Analyst lab (CLI, trace, reconcile, preview, exercises, evidence, scale) | R-043-R-052 | PASS | none defined |

The **automated** column is a test that runs and passes. In the **manual UAT**
column, `DEFINED` means a written procedure exists but no human tester has executed
it (the v1 rows, UAT-001-UAT-010), while `PASS` means the procedure was
manually executed and passed on 2026-07-25 with recorded evidence in the
[recovery UAT execution report](v1.1-recovery-uat-execution-report.md)
(the v1.1 rows, UAT-011-UAT-018).

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
  `FAILED` (a handled `InboundError` - the filing side effects and queue
  resolution are rolled back, then the handled outcome is committed with the
  attempted message preserved as ERRORED, the attempt FAILED, and the queue left
  OPEN), and `REJECTED` (prohibited action or closed item; no processing message).
- **Idempotency and conflict:** matching `request_id` **replay** returns the
  recorded attempt and writes nothing (proven for prior SUCCEEDED, FAILED, and
  REJECTED); a mismatched `request_id` reuse is a `REQUEST_ID_CONFLICT` that
  raises, fabricates nothing, and records exactly one audit event.
- **Terminalization:** a request against a TERMINAL item is rejected, and a
  permitted retry whose target order is now FINALIZED/CANCELLED moves the queue
  `OPEN -> TERMINAL` without filing or reopening the order.
- **History:** `get_recovery_history` returns every attempt in order and excludes
  conflict pseudo-attempts.
- **Rollback:** a handled mid-operation `InboundError` rolls back the filing side
  effects (FISH results, `RESULT_ENTERED`/`INBOUND_RESULT_FILED` events, queue
  resolution) and then commits the approved handled-failure outcome (ERRORED
  message + FAILED attempt, queue OPEN). Separately, an **unexpected**
  non-`InboundError` (before or after filing) rolls back the **whole request** and
  re-raises. Both paths leave no dangling transaction; generic/database errors are
  never converted to FAILED.
- **Durability and integrity:** file-backed durability holds after both a success
  and a handled failure, and `PRAGMA foreign_key_check` returns no violations
  across success, failure, rejection, replay, conflict, and terminalization.

The recovery design, requirements, and test intent are frozen pre-implementation
decision records ([`v1.1-design-record.md`](v1.1-design-record.md),
[`v1.1-requirements.md`](v1.1-requirements.md),
[`v1.1-test-intent.md`](v1.1-test-intent.md)); the implementation evidence above
is recorded here and in the traceability matrix, not in those frozen files.

## Analyst lab evidence

The analyst investigation and recovery lab (`src/analyst/`, issue #43) adds no
recovery semantics and is proven by the eight `test_analyst_*.py` suites:

- **Persistence and errors:** each CLI command runs in a separate process
  against a file database, and a new interpreter reads the results back.
  Invalid arguments, unknown ids, missing or non-UTF-8 files, existing `init`
  targets, and blocked transitions return the documented exit codes and leave
  state correct. Refused preflight requests write nothing. A blocked finalize
  records its validation findings plus `VALIDATION_RUN` and `FINALIZE_BLOCKED`
  audit events. A `REJECTED` recovery records its attempt row and, when the
  target order has since become FINALIZED or CANCELLED, moves the queue item
  `OPEN -> TERMINAL`.
- **Read-only guarantees:** the database file hash is identical before and
  after every read command, and a missing database is never created.
- **Trace and reconciliation:** case links are checked against independent SQL,
  including payload-derived and missing provenance. Every one of the 29
  reconciliation rules fires on a constructed violating state. The suite's
  clean database, built only through the services across filing, failure,
  recovery, conflict, terminal, and export cases, yields no ERROR or WARNING
  finding. Only the expected INFO findings for outstanding prerequisites and
  recorded conflicts appear. A specimen received or rejected after finalization (the workflow
  allows it) is reported as a WF-002 WARNING by design, not as an ERROR.
- **Preview:** the source is unchanged and no request id or audit row is
  consumed. Normalized preview changes equal a real apply from the same
  baseline. When order state changes after a preview, the apply follows the
  current rules, and an injected mid-filing failure rolls back in both the
  preview and the apply.
- **Exercises and evidence:** all seven exercises verify from persisted rows,
  and tampering with any checked value makes verification fail. Reruns
  reproduce the same normalized database and the same evidence content
  fingerprint. Stored payload fingerprints match SHA-256 values computed
  independently from the fixture files.
- **Scale:** a bounded 1,000-message check confirms counts, completeness, and
  ordering, and shows reconciliation and trace statement counts do not grow
  with message volume. Timing is recorded without a pass threshold.

## Analyst-query coverage

All seven `queries/*.sql` views have deterministic result-level assertions. The
analyst lab's `analyst_queue_worklist.sql` is asserted in
`tests/test_analyst_trace.py` and `tests/test_analyst_scale.py`; the six R-019
views are covered as follows.
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
python -m pytest -q                   # -> 600 passed
python -m src.demo_run                 # -> 5 scenarios, exit 0
mkdir lab-work && python -m src.analyst exercise run-all --workdir lab-work/all   # -> 7 verified
```

The v1.1 manual layer (UAT-011-UAT-018) has been executed; its recorded
evidence is in the
[recovery UAT execution report](v1.1-recovery-uat-execution-report.md). To
execute the remaining v1 manual layer (UAT-001-UAT-010), run
[`uat-test-scripts.md`](uat-test-scripts.md) and record results before
claiming a manual pass.

## Conclusion

Within its stated synthetic scope, the simulator meets all 51 requirements,
each backed by result-level automated coverage that runs and passes today. The
41 v1/v1.1 requirements also have a manual UAT, executed and passed for v1.1
recovery (UAT-011-UAT-018) and defined (not executed) for v1; the analyst lab
requirements have automated coverage only. The workflow, validation, outbound generation,
inbound ingestion/error-queue, and controlled recovery behaviors are traceable,
reproducible, and demonstrated end-to-end. This remains an **educational,
synthetic, non-regulatory** portfolio artifact - not a validated medical device,
certified interface engine, or production LIS. Remaining gaps are documented,
bounded, and non-blocking for the project's purpose as a demonstration of
LIS/interface analyst thinking.
