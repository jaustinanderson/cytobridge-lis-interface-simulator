# Requirements-to-test traceability matrix

Maps every requirement to the code that implements it (file/function or schema
constraint), its automated `pytest` coverage, the manual
[UAT script](uat-test-scripts.md) that exercises it, and its current status.
Three requirement sources are traced here:

- **R-001 - R-019** (v1) from [`requirements.md`](requirements.md).
- **R-020 - R-041** (v1.1 controlled recovery) from the frozen, approved
  [`v1.1-requirements.md`](v1.1-requirements.md). The frozen numbering and
  wording are preserved verbatim; this matrix records the implementation
  evidence that closes them, it does not restate or weaken them.
- **R-043 - R-052** (analyst investigation and recovery lab, issue #43) from
  [`requirements.md`](requirements.md). These have automated coverage only;
  no manual UAT is defined for them.

> **Synthetic learning project - no PHI, not a validated device.** The two status
> columns below mean different things and are reported separately, never merged:
> the **automated** status is the result of a `pytest` test that actually runs
> and passes; the **manual UAT** status is either `DEFINED` (a written procedure
> exists but no tester has executed it - UAT-001 - UAT-010) or `PASS` (the
> procedure was manually executed and passed on 2026-07-25 with recorded
> evidence - UAT-011 - UAT-018, see the
> [execution report](v1.1-recovery-uat-execution-report.md)). Test names match
> the current codebase (`pytest` -> **513 passed** across fifteen suites).

**Status legend:**

- **Automated:** `PASS` = the mapped result-level `pytest` test(s) run and pass.
- **Manual UAT:** `DEFINED` = a manual UAT procedure exists in
  [`uat-test-scripts.md`](uat-test-scripts.md) but has not been executed;
  `PASS` = the procedure was manually executed and passed, with recorded
  evidence in the
  [v1.1 recovery UAT execution report](v1.1-recovery-uat-execution-report.md).

The v1 matrix predates the two-column split; its single `Status = PASS` column
means the automated test passes and a UAT is defined (never executed).

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
| **R-009** | Outbound FHIR DiagnosticReport generated only for finalized orders | `src/interfaces/outbound_fhir.py` / `build_diagnostic_report` | `test_outbound_interfaces.py::test_fhir_bundle_contains_diagnostic_report`, `::test_fhir_diagnostic_report_fields`, `::test_fhir_export_blocked_for_non_finalized_order` | UAT-004 | PASS |
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

## v1.1 controlled-recovery matrix (R-020 - R-041)

Maps each frozen v1.1 requirement to its implementing file/function or schema
constraint, its executable `pytest` coverage, and an applicable manual UAT. The
implementation (P3-001 through P3-003) reflects the reviewed P3-003 corrections:
recovery validates the **exact stored classification** against the single
authoritative `inbound_hl7._FAILURE_CLASSIFICATION` mapping before eligibility;
`RETRY_ORIGINAL` sources its payload from the **original `interface_message`
payload** linked by the queue item (never `interface_error_queue.raw_payload`);
and a handled failure **rolls back after filing but before success bookkeeping**,
preserving the attempted message as `ERRORED` and the attempt as `FAILED` while
leaving the queue `OPEN`.

| Req ID | Requirement (short) | Implemented by (file / function or schema constraint) | pytest coverage | UAT | Automated | Manual UAT |
|---|---|---|---|---|---|---|
| **R-020** | Structured `failure_code` / `failure_category` / `recovery_policy` + retained `reason` | `schema.sql` (`interface_error_queue` classification columns + `CHECK`s); `src/interfaces/inbound_hl7.py` (`_FAILURE_CLASSIFICATION` populates them) | `test_recovery_schema.py::test_queue_has_expected_columns`, `::test_approved_classification_values_allowed`; `test_failure_classification.py::test_original_corpus_case_is_classified` | UAT-011 | PASS | PASS |
| **R-021** | Every failure maps to exactly the approved code/category/policy triple; unmappable is a blocker | `src/interfaces/inbound_hl7.py` / `_FAILURE_CLASSIFICATION` (single authoritative mapping) | `test_failure_classification.py::test_corpus_exercises_exactly_14_unique_approved_codes`, `::test_noninteger_and_negative_counts_both_map_to_invalid_cell_count` | UAT-011 | PASS | PASS |
| **R-022** | Original message + queue `raw_payload` never modified by recovery | `src/recovery.py` / `_process`, `_resolve_queue`, `_terminalize_queue` (write only status/timestamps; never rewrite original) | `test_recovery_service.py::test_invariant_I01_original_message_immutability`, `::test_retry_sources_payload_from_linked_original_message` | UAT-012 | PASS | PASS |
| **R-023** | Original message never `FILED`; only a new recovery message may reach `FILED` | `src/recovery.py` / `_process` (new message FILED; original untouched) | `test_recovery_service.py::test_invariant_I01_original_message_immutability` | UAT-012 | PASS | PASS |
| **R-024** | Recovery attempt recorded with approved fields, links `queue_id` and resulting message | `schema.sql` (`interface_recovery_attempt` table + FKs); `src/recovery.py::_insert_attempt` | `test_recovery_schema.py::test_recovery_attempt_has_exactly_approved_fields`, `::test_queue_foreign_key_enforced`, `::test_resulting_message_foreign_key_enforced`; `test_recovery_service.py::test_corrected_redrive_succeeds_for_every_recoverable_case` | UAT-012 | PASS | PASS |
| **R-025** | `RETRY_ORIGINAL` permitted only for OPEN `ORDER_NOT_FOUND`; rejected otherwise | `src/recovery.py::_recover` (retry eligibility) | `test_recovery_service.py::test_retry_original_rejected_for_redrive_only_classes` | UAT-013 | PASS | PASS |
| **R-026** | Permitted retry reuses exact original payload, new message, recorded on attempt | `src/recovery.py::retry_queue_item`, `_original_message_payload` | `test_recovery_service.py::test_unchanged_retry_succeeds_after_order_becomes_available`, `::test_retry_sources_payload_from_linked_original_message` | UAT-013 | PASS | PASS |
| **R-027** | `REDRIVE_CORRECTED` permitted for OPEN `REDRIVE_ONLY`/`RETRY_OR_REDRIVE`; rejected for TERMINAL | `src/recovery.py::_recover` (`_OPEN_POLICIES`; closed-item reject) | `test_recovery_service.py::test_corrected_redrive_succeeds_for_every_recoverable_case`, `::test_recovery_rejected_for_terminal_queue_item` | UAT-012 | PASS | PASS |
| **R-028** | Permitted re-drive stores corrected payload on a distinct new message, original not overwritten | `src/recovery.py::redrive_queue_item`, `_process` | `test_recovery_service.py::test_corrected_redrive_succeeds_for_every_recoverable_case`, `::test_invariant_I01_original_message_immutability` | UAT-012 | PASS | PASS |
| **R-029** | Any recovery against a TERMINAL item rejected; order never reopened/unfinalized/uncancelled | `src/recovery.py::_recover` (closed-item reject), `_process` (dynamic OPEN -> TERMINAL) | `test_recovery_service.py::test_recovery_rejected_for_terminal_queue_item`, `::test_open_to_terminal_when_target_order_now_terminal` | UAT-014 | PASS | PASS |
| **R-030** | Status in OPEN/RESOLVED/TERMINAL; only OPEN->RESOLVED and OPEN->TERMINAL; no auto reopen | `schema.sql` (`status` `CHECK`); `src/recovery.py::_resolve_queue`, `_terminalize_queue` | `test_recovery_schema.py::test_valid_queue_state_timestamp_combinations`, `::test_invalid_queue_status_value_fails`; `test_recovery_service.py::test_open_to_terminal_when_target_order_now_terminal` | UAT-014 | PASS | PASS |
| **R-031** | Queue timestamp consistency per state (OPEN/RESOLVED/TERMINAL) | `schema.sql` (state/timestamp `CHECK`) | `test_recovery_schema.py::test_valid_queue_state_timestamp_combinations`, `::test_invalid_queue_state_timestamp_combinations_fail` | UAT-018 | PASS | PASS |
| **R-032** | Outcome SUCCEEDED/FAILED/REJECTED with correct resulting-message + ERRORED/OPEN semantics | `src/recovery.py::_process`; `schema.sql` (outcome/resulting-message `CHECK`) | `test_recovery_service.py::test_failed_then_later_success_with_new_request_id`; `test_recovery_schema.py::test_succeeded_without_resulting_message_fails`, `::test_rejected_with_resulting_message_fails` | UAT-015 | PASS | PASS |
| **R-033** | Recovery-attempt history retrievable in order | `src/recovery.py::get_recovery_history` | `test_recovery_service.py::test_get_recovery_history_orders_attempts_and_excludes_conflicts` | UAT-017 | PASS | PASS |
| **R-034** | Matching `request_id` replay returns existing outcome, creates/changes nothing | `src/recovery.py::_recover` (replay match on queue/action/sha/actor) | `test_recovery_service.py::test_invariant_I02_duplicate_and_replay_protection`, `::test_matching_replay_of_prior_failed_attempt`, `::test_matching_replay_of_prior_rejected_attempt` | UAT-016 | PASS | PASS |
| **R-035** | At most one SUCCEEDED attempt per item, enforced independently of `request_id` | `schema.sql` (`idx_recovery_attempt_single_success` partial unique index) | `test_recovery_schema.py::test_second_succeeded_for_same_queue_fails`; `test_recovery_service.py::test_invariant_I02_duplicate_and_replay_protection` | UAT-016 | PASS | PASS |
| **R-036** | New `request_id` against a RESOLVED item is REJECTED; no message/FISH/filing | `src/recovery.py::_recover` (status != OPEN -> `_reject`) | `test_recovery_service.py::test_invariant_I02_duplicate_and_replay_protection` | UAT-016 | PASS | PASS |
| **R-037** | A FAILED attempt may be retried later with a new `request_id` while OPEN | `src/recovery.py::_recover`, `_process` (FAILED leaves OPEN; new id processes) | `test_recovery_service.py::test_failed_then_later_success_with_new_request_id` | UAT-015 | PASS | PASS |
| **R-038** | Unit commit; handled-failure rollback preserves ERRORED message + FAILED attempt, queue OPEN | `src/recovery.py::_process` (savepoint rollback / atomic commit; full-request rollback boundary) | `test_recovery_service.py::test_handled_mid_operation_failure_rolls_back_all_side_effects`, `::test_unexpected_failure_after_filing_rolls_back_entire_request`, `::test_unexpected_failure_rolls_back_and_reraises` | UAT-015 | PASS | PASS |
| **R-039** | Headless Python service boundary (retry / redrive / history); no UI/API/CLI/framework | `src/recovery.py` / `retry_queue_item`, `redrive_queue_item`, `get_recovery_history` | `test_recovery_service.py::test_normal_inbound_ingestion_still_files_unchanged` (and the suite exercises the three public functions) | UAT-018 | PASS | PASS |
| **R-040** | Audit + troubleshooting evidence (`payload_sha256`, `outcome_detail`) explains each outcome | `src/recovery.py` (`payload_sha256`, `outcome_detail`, `_record_conflict`); `workflow.record_audit` | `test_recovery_service.py::test_get_recovery_history_orders_attempts_and_excludes_conflicts`, `::test_conflict_on_different_payload_sha256` | UAT-017 | PASS | PASS |
| **R-041** | Mismatched `request_id` reuse rejected as `REQUEST_ID_CONFLICT`, audit-only, original untouched | `src/recovery.py::_recover`, `_record_conflict` (`RequestIdConflictError`) | `test_recovery_service.py::test_conflict_on_different_queue_id`, `::test_conflict_on_different_action`, `::test_conflict_on_different_payload_sha256`, `::test_conflict_on_different_actor`, `::test_conflict_checked_before_queue_or_action_eligibility` | UAT-016 | PASS | PASS |

### Note on the frozen requirement source

R-020 through R-041 are quoted by ID from the frozen
[`v1.1-requirements.md`](v1.1-requirements.md), which is a **pre-implementation
decision record** and is not edited by this task. That file still describes the
behavior in the future tense it was frozen with; the "Implemented by" and
"pytest coverage" columns above are the current-state evidence that the behavior
now exists, recorded here rather than in the frozen file. The demonstration in
`python -m src.demo_run` (scenario 5) shows representative recovery cases; the
`test_recovery_service.py` suite is what proves all twelve corrected corpus
re-drives and both human-approved invariants (I-01, I-02).

Issue #43 later authorized an additive, headless analyst CLI
(`python -m src.analyst`, R-043 - R-052) whose `recover` commands call these
same three service functions and add no recovery semantics. The frozen R-039
wording ("no web UI, REST API, full CLI, or unrelated framework") is preserved
here as the v1.1 record, and R-039's evidence still exercises the service
functions directly. Whether the frozen wording should be amended to mention
the later CLI is Austin's decision; no frozen file is changed.

## Analyst lab matrix (R-043 - R-052)

Maps each analyst investigation and recovery lab requirement (issue #43) to its
implementation and automated coverage. No manual UAT is defined for these
requirements. The seven executable exercises are **automated** evidence: an
automated exercise run is not a manual UAT pass and does not demonstrate
Austin's mastery.

| Req ID | Requirement (short) | Implemented by (file / function) | pytest coverage | UAT | Automated |
|---|---|---|---|---|---|
| **R-043** | Analyst CLI: help, exit codes, readable text, one JSON envelope; mutations only through existing services | `src/analyst/cli.py` / `main`, `build_parser`; `src/analyst/common.py` (exit codes) | `test_analyst_cli.py::test_envelope_command_words_and_results`, `::test_usage_errors_exit_2`, `::test_help_exits_0`, `::test_text_mode_output_for_every_command`, `::test_unexpected_exception_exit_6` | none | PASS |
| **R-044** | `init` refuses existing paths; read commands never create or modify; writes never create | `src/analyst/store.py` / `init_database`, `open_read_only`, `open_read_write` | `test_analyst_cli.py::test_init_refuses_existing_path`, `::test_missing_database_is_not_created`, `::test_read_only_commands_leave_database_identical`, `::test_non_cytobridge_database_exit_5` | none | PASS |
| **R-045** | State persists across separate invocations and process restart | `src/analyst/cli.py` (each command opens and closes its own connection) | `test_analyst_cli.py::test_each_command_in_separate_process_persists`, `::test_process_restart_data_visible_from_new_interpreter` | none | PASS |
| **R-046** | Exact strict-UTF-8 message files; stored payload SHA-256 equals file-byte SHA-256; bad input refused before writing | `src/analyst/common.py` / `read_payload_file`; `cli.py` ingest/redrive handlers | `test_analyst_cli.py::test_ingest_preserves_exact_payload_bytes`, `::test_non_utf8_input_exit_5_database_unchanged`, `::test_missing_input_file_exit_3` | none | PASS |
| **R-047** | Case trace over real keys; payload-derived and audit-text links labeled; upsert and attribution limits stated | `src/analyst/trace.py` / `build_case`, `resolve_anchor`, `payload_accession` | `test_analyst_trace.py::test_foreign_key_links_exist_and_are_complete`, `::test_case_records_match_stored_rows`, `::test_order_not_found_message_later_matched_by_payload`, `::test_finalized_order_rejection_is_linked_only_by_payload`, `::test_same_probe_filed_twice_lists_both_candidates`, `::test_forged_audit_reference_is_unresolved` | none | PASS |
| **R-048** | Deterministic reconciliation findings tied to documented rules; exit 1 on ERROR | `src/analyst/reconcile.py` / `RULES`, `reconcile_database`, `reconcile_case` | `test_analyst_reconcile.py::test_clean_database_has_no_error_or_warning_findings`, `::test_rule_fires_on_violating_state`, `::test_every_rule_has_a_negative_case`, `::test_statement_count_is_constant_as_rows_grow`; `test_analyst_cli.py::test_reconcile_error_exit_1` | none | PASS |
| **R-049** | Non-mutating preview with the unchanged service; matches a real apply; stale previews do not bind apply | `src/analyst/preview.py` / `preview_recovery`, `snapshot_tables`, `diff_snapshots`, `normalize_changes` | `test_analyst_preview.py::test_preview_never_mutates_source`, `::test_preview_does_not_consume_request_id`, `::test_preview_matches_real_apply_from_same_baseline`, `::test_stale_success_preview_then_order_finalized_apply_is_rejected`, `::test_stale_failed_retry_preview_then_order_created_apply_succeeds`, `::test_handled_failure_rolls_back_filing_in_preview` | none | PASS |
| **R-050** | Seven exercises; instructions separate from answer keys; database-querying verifier; isolated reruns | `src/analyst/exercises.py` / `run_exercise`, `verify_exercise`, `run_all`; `exercises/` | `test_analyst_exercises.py::test_run_exercise_end_to_end_is_deterministic`, `::test_verify_never_consults_the_cli`, `::test_tamper_attempt_outcome` (and the other tamper tests), `::test_analyst_instructions_contain_no_answer_key_text`, `::test_exercises_do_not_cross_contaminate`, `::test_run_all_passes_and_refuses_a_reused_workdir` | none | PASS |
| **R-051** | Deterministic synthetic case evidence with source revision, fingerprints, outcomes, history, limitations | `src/analyst/evidence.py` / `build_evidence`, `render_report`, `content_fingerprint`, `resolve_source_revision` | `test_analyst_evidence.py::test_json_is_ascii_parses_and_is_byte_identical_across_builds`, `::test_fingerprints_match_fixture_file_bytes_and_checks_pass`, `::test_content_fingerprint_is_stable_across_wall_clock_seconds`, `::test_report_is_short_ascii_markdown_with_required_content`, `::test_every_evidence_document_is_marked_synthetic` | none | PASS |
| **R-052** | Bounded 1,000-message scale check; statement counts do not grow with volume; timing recorded only | `tests/test_analyst_scale.py` over `trace`, `reconcile`, `evidence`, and `queries/analyst_queue_worklist.sql` | `test_analyst_scale.py::test_scale_message_and_queue_counts`, `::test_scale_worklist_lists_every_queue_item_once_in_order`, `::test_scale_reconcile_clean_and_deterministic`, `::test_scale_statement_counts_do_not_grow_with_message_count` | none | PASS |

## Coverage summary

| Category | Requirements | Automated PASS | Manual UAT |
|---|---|---|---|
| Workflow (v1) | R-001, R-002 | 2 | 2 DEFINED |
| Validation (v1) | R-003 - R-006 | 4 | 4 DEFINED |
| Audit (v1) | R-007, R-016 | 2 | 2 DEFINED |
| Interface outbound (v1) | R-008, R-009 | 2 | 2 DEFINED |
| Interface inbound (v1) | R-010 - R-015, R-017, R-018 | 8 | 8 DEFINED |
| Analyst query (v1) | R-019 | 1 | 1 DEFINED |
| Recovery classification (v1.1) | R-020, R-021 | 2 | 2 PASS |
| Recovery immutability/lineage (v1.1) | R-022 - R-024 | 3 | 3 PASS |
| Recovery retry/re-drive/terminal (v1.1) | R-025 - R-029 | 5 | 5 PASS |
| Recovery state model (v1.1) | R-030, R-031 | 2 | 2 PASS |
| Recovery outcomes/history (v1.1) | R-032, R-033 | 2 | 2 PASS |
| Recovery idempotency/atomicity (v1.1) | R-034 - R-038, R-041 | 6 | 6 PASS |
| Recovery service/audit (v1.1) | R-039, R-040 | 2 | 2 PASS |
| Analyst lab (#43) | R-043 - R-052 | 10 | none defined |
| **Total** | **51** | **51** | **19 DEFINED (v1) + 22 PASS (v1.1); 10 automated only (#43)** |

Every v1 and v1.1 requirement traces to at least one automated `pytest` test
**and** a manual UAT script; the analyst lab requirements (R-043 - R-052) have
automated coverage only. The automated layer runs and passes today (`pytest` ->
513 passed across fifteen suites). The v1.1 manual layer (UAT-011 - UAT-018, mapped
to R-020 - R-041) was manually executed and passed on 2026-07-25, with the
recorded evidence in the
[execution report](v1.1-recovery-uat-execution-report.md); the v1 manual layer
(UAT-001 - UAT-010) is defined but not executed - run it from
[`uat-test-scripts.md`](uat-test-scripts.md) and record the results before
claiming a manual pass.
