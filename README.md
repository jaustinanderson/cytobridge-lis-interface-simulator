# CytoBridge — LIS Interface Simulator

A synthetic cytogenetics/FISH **Laboratory Information System (LIS) + interface
simulator**. It demonstrates SQL schema design, an order/specimen/result
workflow, validation rules, audit trails, HL7/FHIR-style interface thinking, an
inbound error queue, and requirements-to-test traceability.

This is an **analyst-first portfolio project**, not a polished web app. It is
"Beaker-adjacent" in spirit — it is **not** an Epic clone and contains no Epic
content.

> **Data notice:** All data in this project is **synthetic**. No PHI. No real
> patient data.

## Scope (v1)

v1 models exactly one panel — **AML/MDS FISH** — end to end, headless:

1. Create a synthetic patient.
2. Create an AML/MDS FISH order.
3. Receive and accession a bone-marrow specimen.
4. Enter structured, per-probe FISH results.
5. Run validation.
6. Block finalization if a required probe is missing.
7. Record validation errors.
8. Finalize the report when validation passes.
9. Audit every important state change.

Steps 9–13 of the full vision — generating an HL7 ORU-style outbound message,
a FHIR `DiagnosticReport`, and ingesting/routing inbound instrument messages —
are **planned for a later session**. The database **schema already provisions**
the `interface_message` and `interface_error_queue` tables and the matching
analyst queries, but the Python for those interfaces is not in v1.

### Technology

- **Python** (standard library only at runtime).
- **SQLite** via the stdlib `sqlite3` module.
- Raw, hand-written SQL in `schema.sql` and `queries/`. **No ORM.**
- **pytest** for tests.
- Headless workflow first — no Streamlit UI until the workflow is solid.

## Repository layout

```
schema.sql              DDL: PK/FK/CHECK constraints + AML/MDS panel seed data
queries/                Analyst SQL (parameterized where appropriate)
  turnaround_time.sql       TAT for finalized orders (hours)
  pending_review.sql        Orders awaiting review, STAT first
  stat_pending.sql          Un-finalized STAT orders, aging
  validation_error_rate.sql Share of orders with blocking errors
  audit_lookup.sql          Full audit trail for one order (bind :order_id)
  interface_error_queue.sql Open inbound interface errors
src/
  db.py                 sqlite3 helpers; parameterized queries; query loader
  workflow.py           patient/order/specimen/result/finalize + audit
  validation.py         validation rules (returns typed findings)
  reports.py            report summary + seam for a future ISCN parser
  demo_run.py           headless happy path + missing-probe failure
tests/
  test_workflow.py      workflow lifecycle + audit + constraints
  test_validation.py    validation rules
```

## Data model highlights

- **Real constraints:** primary keys, foreign keys (`PRAGMA foreign_keys = ON`),
  and `CHECK` constraints — e.g. `cells_abnormal <= cells_scored`, enumerated
  order/specimen/interpretation statuses, and a rejected specimen must carry a
  reason.
- **One result per probe per order** (`UNIQUE (order_id, probe_id)`);
  re-entering a probe updates the existing row.
- **Audit trail** (`audit_event`) records every state change with entity, action,
  actor, and detail.
- **Validation findings** are typed (`ERROR` blocks finalize; `WARNING` is
  advisory) and persisted to `validation_error`.

## Validation rules (v1)

| rule_code            | severity   | meaning                                                          |
|----------------------|------------|------------------------------------------------------------------|
| `SPEC_ACCESSIONED`   | ERROR      | Specimen must be received and accessioned (not rejected/missing) |
| `MISSING_PROBE`      | ERROR      | Every required probe must have a result                          |
| `ABN_EXCEEDS_SCORED` | ERROR      | Abnormal cells cannot exceed scored cells                        |
| `INTERP_CONSISTENCY` | ERROR/WARN | Interpretation must agree with percent-abnormal vs probe cutoff  |
| `CELL_COUNT_LOW`     | WARNING    | Scored-cell count below the minimum threshold                    |

The consistency check is **cutoff-aware**: a percent-abnormal at/above a probe's
`normal_cutoff` that is still called `NORMAL` is a blocking error (missed
abnormal), while an `ABNORMAL` call below cutoff is an advisory warning.

## Run the demo

From the repo root:

```bash
python -m src.demo_run
```

This runs two scenarios against a fresh in-memory database: a complete order that
passes validation and finalizes (with report summary and audit trail printed),
and an order missing a required probe whose finalization is **blocked** with the
validation findings shown.

## Run the tests

```bash
pip install -r requirements-dev.txt   # pytest only
pytest
```

## Roadmap (not in v1)

- HL7 ORU-style outbound message generation.
- FHIR `DiagnosticReport` JSON generation.
- Inbound instrument ORU ingestion: file valid messages to open orders; route
  malformed/unmatched messages to the interface error queue with a clear reason.
- ISCN nomenclature parser (seam already present in `reports.py`).
- Optional Streamlit UI once the workflow is proven.
