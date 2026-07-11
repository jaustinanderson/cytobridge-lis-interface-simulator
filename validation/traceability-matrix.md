# Requirements-to-test traceability matrix

Maps every requirement (see [`requirements.md`](requirements.md)) to the code
that implements it, its automated `pytest` coverage, the manual
[UAT script](uat-test-scripts.md) that exercises it, and its current status.

> **Synthetic learning project - no PHI, not a validated device.** "Verified"
> below means the mapped `pytest` test passes and the UAT script has a defined
> manual procedure - not regulatory validation. Test names match current
> `main` (`pytest` -> **61 passed**).

**Status legend:** PASS = verified (result-level automated test passing + UAT
defined).

## Matrix

| Req ID | Requirement (short) | Implemented by (file / function) | pytest coverage | UAT | Status |
|---|---|---|---|---|---|
| **R-001** | Synthetic patient + order creation; new order is `ORDERED` | `src/workflow.py` / `create_patient`, `create_order` | `test_workflow.py::test_create_patient_and_order` | UAT-001 | PASS |
| **R-002** | Specimen received + accessioned; order leaves `ORDERED` | `src/workflow.py` / `receive_specimen`, `accession_specimen` | `test_workflow.py::test_receive_and_accession_specimen` | UAT-001 | PASS |
| **R-003** | Required-probe validation (`MISSING_PROBE`) | `src/validation.py` / `validate_order` | `test_validation.py::test_missing_required_probe_is_error`; `test_workflow.py::test_missing_probe_blocks_finalization` | UAT-002 | PASS |
| **R-004** | Abnormal cells <= scored cells (`CHECK` + `ABN_EXCEEDS_SCORED`) | `schema.sql` (`fish_result` CHECK) / `src/validation.py::validate_order` | `test_workflow.py::test_schema_rejects_abnormal_exceeding_scored` | UAT-002 | PASS |
| **R-005** | Cutoff-aware interpretation consistency (`INTERP_CONSISTENCY`) | `src/validation.py` / `validate_order` | `test_validation.py::test_abnormal_above_cutoff_called_normal_is_error`, `::test_below_cutoff_called_abnormal_is_warning` | UAT-002 | PASS |
| **R-006** | Finalization blocked on any `ERROR`; findings persisted | `src/workflow.py` / `finalize_order`, `run_validation`; `src/validation.py::has_blocking_errors` | `test_workflow.py::test_missing_probe_blocks_finalization`, `::test_blocked_finalize_persists_validation_errors` | UAT-002 | PASS |
| **R-007** | Audit event on every state change | `src/workflow.py` / `record_audit` (called across workflow) | `test_workflow.py::test_audit_trail_records_state_changes` | UAT-009 | PASS |
| **R-008** | Outbound HL7 ORU generated only for finalized orders | `src/interfaces/outbound_hl7.py` / `generate_oru`; `src/interfaces/__init__.py::collect_report_data` | `test_outbound_interfaces.py::test_hl7_includes_required_segments`, `::test_hl7_export_blocked_for_non_finalized_order` | UAT-003 | PASS |
| **R-009** | Outbound FHIR DiagnosticReport generated only for finalized orders | `src/interfaces/outbound_fhir.py` / `build_diagnostic_report` | `test_outbound_interfaces.py::test_fhir_bundle_contains_diagnostic_report`, `::test_fhir_export_blocked_for_non_finalized_order` | UAT-004 | PASS |
| **R-010** | Every inbound message stored in `interface_message` | `src/interfaces/inbound_hl7.py` / `ingest_message`; `__init__.py::store_message` | `test_inbound_interfaces.py::test_valid_inbound_stored_in_interface_message`, `::test_failed_inbound_still_stored_in_interface_message` | UAT-005 | PASS |
| **R-011** | Valid inbound ORU files per-probe results to open order | `src/interfaces/inbound_hl7.py` / `ingest_message`, `_file_results` | `test_inbound_interfaces.py::test_valid_inbound_files_probe_results` | UAT-005 | PASS |
| **R-012** | Unmatched accession -> error queue | `src/interfaces/inbound_hl7.py` / `_match_order`, `_route_to_error_queue` | `test_inbound_interfaces.py::test_unmatched_accession_goes_to_error_queue` | UAT-006 | PASS |
| **R-013** | Missing `OBX` -> error queue | `src/interfaces/inbound_hl7.py` / `parse_message` | `test_inbound_interfaces.py::test_missing_obx_goes_to_error_queue` | UAT-007 | PASS |
| **R-014** | Malformed numeric result -> error queue (all-or-nothing) | `src/interfaces/inbound_hl7.py` / `_validate_obx`, `_parse_count` | `test_inbound_interfaces.py::test_invalid_numeric_result_goes_to_error_queue` | UAT-008 | PASS |
| **R-015** | Inbound to already-finalized order rejected to error queue | `src/interfaces/inbound_hl7.py` / `_match_order` | `test_inbound_interfaces.py::test_already_finalized_order_goes_to_error_queue` | UAT-006 | PASS |
| **R-016** | `INBOUND_RESULT_FILED` audit event on successful filing | `src/interfaces/inbound_hl7.py` / `_file_results`; `workflow.record_audit` | `test_inbound_interfaces.py::test_audit_event_recorded_for_inbound_filing` | UAT-009 | PASS |
| **R-017** | Unknown probe code for panel -> error queue | `src/interfaces/inbound_hl7.py` / `_validate_obx` | `test_inbound_interfaces.py::test_unknown_probe_code_goes_to_error_queue` | UAT-008 | PASS |
| **R-018** | Error-queue entry has message_id/direction/reason/OPEN/created | `src/interfaces/inbound_hl7.py` / `_route_to_error_queue`; `schema.sql` (`interface_error_queue`) | `test_inbound_interfaces.py::test_error_queue_entry_has_expected_fields` | UAT-006, UAT-007, UAT-008 | PASS |
| **R-019** | Analyst SQL worklist views | `queries/*.sql`; `src/db.py::run_query` | `test_queries.py` (pending review, STAT aging, turnaround, error rate, audit lookup); `test_inbound_interfaces.py::test_error_queue_query_lists_open_items` | UAT-010 | PASS |

### Note on R-019

Six analyst queries ship under `queries/` (`pending_review.sql`,
`stat_pending.sql`, `turnaround_time.sql`, `validation_error_rate.sql`,
`audit_lookup.sql`, `interface_error_queue.sql`).
`interface_error_queue.sql` is asserted in `test_inbound_interfaces.py`; the
other five views have deterministic result assertions in `test_queries.py`.
Together they verify row selection, priority/order behavior, counts, calculated
metrics, and parameterized order scoping. The original KI-01 gap is recorded as
resolved in [`known-issues.md`](known-issues.md).

## Coverage summary

| Category | Requirements | Fully verified | Partial |
|---|---|---|---|
| Workflow | R-001, R-002 | 2 | 0 |
| Validation | R-003 - R-006 | 4 | 0 |
| Audit | R-007, R-016 | 2 | 0 |
| Interface (outbound) | R-008, R-009 | 2 | 0 |
| Interface (inbound) | R-010 - R-015, R-017, R-018 | 8 | 0 |
| Analyst query | R-019 | 1 | 0 |
| **Total** | **19** | **19** | **0** |

Every requirement traces to at least one automated test **and** a manual UAT
script. Re-run automated coverage with `pytest`; execute the manual layer with
[`uat-test-scripts.md`](uat-test-scripts.md).
