# Change-control log

A change history for the CytoBridge LIS Interface Simulator, organized by
development session. Each session was developed on its own branch and merged to
`main` via a pull request — this log is the human-readable summary of that trail.

> **Synthetic learning project — no PHI.** This is a lightweight change log for a
> portfolio project, not a regulated change-control record. Commit SHAs are from
> the project's git history and may be abbreviated.

## Summary

| Session | Theme | Key additions | Merge commit |
|---|---|---|---|
| S1 | Workflow foundation | Schema, order/specimen/result/finalize workflow, validation rules, audit trail, analyst queries | `d71ad7f` (Merge Session 1) |
| S2 | Outbound interfaces | HL7 ORU^R01-style + FHIR DiagnosticReport-style generation, finalized-only gating | `5a0fd69` (Merge Session 2) |
| S3 | Inbound + error queue | Inbound ORU-style ingestion, accession matching, error-queue routing | `19bf8da` (Merge Session 3) |
| S4 | Validation package | This documentation set (requirements, traceability, UAT, risk, demo/diagram/portfolio) | *(this PR)* |

---

## Session 1 — AML/MDS FISH workflow foundation

**Requirements introduced:** R-001, R-002, R-003, R-004, R-005, R-006, R-007,
R-019 (analyst queries).

**What changed:**
- `schema.sql` — full v1 data model: `panel`/`probe` reference data, `patient`,
  `lab_order`, `specimen`, `fish_result`, `validation_error`, `report`,
  `audit_event`, and the interface tables (`interface_message`,
  `interface_error_queue`) provisioned for later sessions. Real PK/FK/CHECK
  constraints (incl. `cells_abnormal <= cells_scored`).
- `src/workflow.py` — patient/order/specimen/result lifecycle + finalize, each
  step writing an `audit_event`.
- `src/validation.py` — typed findings for `SPEC_ACCESSIONED`, `MISSING_PROBE`,
  `ABN_EXCEEDS_SCORED`, `INTERP_CONSISTENCY` (cutoff-aware), `CELL_COUNT_LOW`.
- `src/reports.py` — report summary + ISCN parser seam.
- `src/db.py` — `sqlite3` helpers + query loader.
- `queries/` — analyst SQL (`pending_review`, `stat_pending`, `turnaround_time`,
  `validation_error_rate`, `audit_lookup`, `interface_error_queue`).
- `tests/` — `test_workflow.py`, `test_validation.py`.
- `src/demo_run.py` — happy path + blocked-finalize scenarios.

**Representative commits:** `e12f479` (scaffold), `74ee9f4` (docs/column
cleanup), `39f53e0` (README wording), `d71ad7f` (merge).

---

## Session 2 — Outbound HL7 + FHIR generation

**Requirements introduced:** R-008, R-009.

**What changed:**
- `src/interfaces/__init__.py` — `collect_report_data` (single finalized-order
  snapshot), `store_message`, shared `ProbeResult`/`OutboundReportData` types,
  and `OutboundError` gating (finalized-only, fail-loud on missing data).
- `src/interfaces/outbound_hl7.py` — `generate_oru` / `store_oru`
  (MSH/PID/OBR/SPM/OBX, `MSH-11 = T`).
- `src/interfaces/outbound_fhir.py` — `build_diagnostic_report` /
  `generate_diagnostic_report_json` / `store_diagnostic_report` (Bundle:
  Patient, Specimen, per-probe Observations, DiagnosticReport).
- `sample_messages/outbound/` — sample HL7 + FHIR messages.
- `docs/interface-mapping.md` — outbound field-by-field mapping.
- `tests/test_outbound_interfaces.py` — generation + finalized-only gating.
- `src/demo_run.py` — outbound export scenario.

**Representative commits:** `ceaa8c8` (add outbound), `06d8d41` (FHIR performer +
HL7 CR doc note), `a2b3127` (validation-table rendering), `5a0fd69` (merge).

---

## Session 3 — Inbound ingestion + interface error queue

**Requirements introduced:** R-010, R-011, R-012, R-013, R-014, R-015, R-016,
R-017, R-018.

**What changed:**
- `src/interfaces/inbound_hl7.py` — `ingest_message` / `ingest_file` /
  `parse_message`; accession matching to a non-finalized order; all-or-nothing
  OBX validation (`_validate_obx`, `_parse_count`); filing via
  `workflow.enter_fish_result`; `INBOUND_RESULT_FILED` audit event;
  `_route_to_error_queue` for every routing reason (missing/unmatched accession,
  finalized order, missing segment, no OBX, unknown probe, abnormal > scored,
  malformed numeric, incompatible specimen).
- `src/interfaces/__init__.py` — re-export the inbound API.
- `sample_messages/inbound/` — valid, unmatched accession, missing OBX, and
  malformed-value samples.
- `docs/interface-mapping.md` — inbound mapping + routing-reason table.
- `docs/interface-troubleshooting.md` — analyst runbook.
- `tests/test_inbound_interfaces.py` — success + failure paths.
- `src/demo_run.py` — inbound ingestion scenario (scenario 4).

**Representative commits:** `5ca16d6` (add inbound), `19bf8da` (merge).

**No schema change** — reused the S1-provisioned interface tables.

---

## Session 4 — Validation, UAT, and portfolio documentation

**Requirements introduced:** none (documentation only). Consolidates S1–S3 into a
validation package.

**What changed (docs only):**
- `validation/` — `requirements.md`, `traceability-matrix.md`,
  `uat-test-scripts.md`, `validation-summary.md`, `known-issues.md`,
  `change-control-log.md` (this file), `risk-assessment.md`.
- `docs/` — `demo-script.md`, `workflow-diagram.md`, `portfolio-review.md`.
- `README.md` — links to the new validation + demo documentation.

**Code changes:** none intended (documentation-only session). Any code touch
would be limited to fixing a broken documentation reference and called out
explicitly in the PR.

## Change-control principles used

- **One session = one branch = one PR**, never committed straight to `main`.
- **Tests accompany behavior** — every code session added a matching `tests/`
  suite; the suite is green before merge (`pytest` → 56 passed at S3).
- **Scope guardrails** honored every session (no UI/Docker/CI/ORM/new
  panels/new DB/real HL7-FHIR deps/Epic content; synthetic data only).
- **Docs kept in step** — `README.md`, `docs/interface-mapping.md`, and (S4) the
  validation package track the code as it grows.
