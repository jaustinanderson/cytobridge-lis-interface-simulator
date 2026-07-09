# Validation summary

A one-page summary of how the CytoBridge LIS Interface Simulator is validated,
what the current results are, and what "validated" does and does not mean here.

> **Synthetic, analyst-first learning project — no PHI, not a medical device.**
> This summary demonstrates a *validation mindset* (numbered requirements,
> traceability, automated + manual coverage, documented limitations). It is
> **not** a regulatory validation deliverable and confers no clinical fitness.
> This is **Beaker-adjacent learning, not Epic build experience**
> ([`docs/portfolio-review.md`](../docs/portfolio-review.md)).

## Object under validation

| Item | Value |
|---|---|
| System | CytoBridge LIS Interface Simulator |
| Scope | AML/MDS FISH order → result → validation → finalize → outbound HL7/FHIR → inbound ORU ingestion + error queue |
| Sessions covered | 1 (workflow/validation/audit), 2 (outbound HL7/FHIR), 3 (inbound + error queue) |
| Platform | Python (stdlib), SQLite (`sqlite3`), raw SQL, `pytest` |
| Data | 100% synthetic; no PHI |

## Validation approach

Validation is layered:

1. **Requirements** — 19 numbered, testable requirements (`R-001`–`R-019`) in
   [`requirements.md`](requirements.md).
2. **Automated tests** — `pytest` across four suites (`test_workflow.py`,
   `test_validation.py`, `test_outbound_interfaces.py`,
   `test_inbound_interfaces.py`).
3. **Manual UAT** — 10 analyst-style acceptance scripts (`UAT-001`–`UAT-010`)
   in [`uat-test-scripts.md`](uat-test-scripts.md).
4. **Traceability** — every requirement mapped to code, test, and UAT in the
   [traceability matrix](traceability-matrix.md).
5. **Executable demonstration** — `python -m src.demo_run` exercises the happy
   path, a blocked finalize, outbound export, and inbound ingestion/error queue
   against a fresh in-memory database.

## Results (as of the Session 4 branch)

| Metric | Result |
|---|---|
| Automated tests | **56 passed** (`pytest`) |
| Requirements traced | 19 / 19 mapped to code + test + UAT |
| Requirements fully verified (✅) | 18 / 19 |
| Requirements partial (🟡) | 1 / 19 (R-019 — see below) |
| Demo scenarios | 4 / 4 run clean (`python -m src.demo_run`, exit 0) |
| Reproducibility | Deterministic — in-memory DB seeded from `schema.sql`; fixed sample messages |

### Requirement-category coverage

| Category | Requirements | Status |
|---|---|---|
| Workflow (create, accession) | R-001, R-002 | ✅ |
| Validation (probe, counts, cutoff, blocking) | R-003–R-006 | ✅ |
| Audit (workflow + inbound) | R-007, R-016 | ✅ |
| Outbound interfaces (HL7, FHIR, finalized-only) | R-008, R-009 | ✅ |
| Inbound interfaces (store, file, error-queue routing) | R-010–R-015, R-017, R-018 | ✅ |
| Analyst SQL views | R-019 | 🟡 partial |

## Known limitation affecting coverage

**R-019 (analyst queries)** is marked partial: five of the six `queries/*.sql`
views are exercised for runnability (via `demo_run.py`) but only
`interface_error_queue.sql` has a dedicated result-asserting test. Tracked as
**KI-01** in [`known-issues.md`](known-issues.md). All other requirements have a
direct automated assertion.

Broader boundaries (educational HL7/FHIR only, single panel, no auth/security
model, in-memory demo DB) are enumerated in [`known-issues.md`](known-issues.md)
and assessed in [`risk-assessment.md`](risk-assessment.md).

## How to reproduce

```bash
pip install -r requirements-dev.txt   # pytest only
pytest                                 # -> 56 passed
python -m src.demo_run                 # -> 4 scenarios, exit 0
```

Then execute the manual layer with [`uat-test-scripts.md`](uat-test-scripts.md)
and record results in its summary table.

## Conclusion

Within its stated synthetic scope, the simulator meets all 19 requirements, with
18 fully verified by automated tests and one partially covered (analyst-query
assertions). The workflow, validation, outbound generation, and inbound
ingestion/error-queue behaviors are traceable, reproducible, and demonstrated
end-to-end. Remaining gaps are documented, bounded, and non-blocking for the
project's purpose as a portfolio demonstration of LIS/interface analyst thinking.
